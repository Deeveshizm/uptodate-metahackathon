"""Deploy the API and private task worker; credentials are sent via stdin only.

Run from the repository root with the project's .venv Python. Runtime secrets
are selected explicitly, stored in Secret Manager, and pinned by version.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from scripts.cloud_settings import load_settings


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','build','deploy'])
    parser.add_argument('--project',required=True)
    parser.add_argument('--region',default='us-east4')
    parser.add_argument('--env-file',default='.env.local')
    parser.add_argument('--image',help='Previously built immutable image tag')
    parser.add_argument('--cache-image',help='Previous image to reuse dependency layers during a rebuild')
    args=parser.parse_args()
    cli=shutil.which('gcloud') or str(Path.home()/'dev/tools/google-cloud-sdk/bin/gcloud')
    env={**os.environ,'CLOUDSDK_PYTHON':sys.executable}
    def run(*command,input=None,check=True,quiet=False):
        result=subprocess.run([cli,*command,'--project',args.project,'--quiet'],input=input,capture_output=True,text=True,env=env,cwd=ROOT)
        if check and result.returncode:
            # gcloud commands never receive secrets in command-line arguments.
            print(result.stderr[-5000:],file=sys.stderr)
            raise RuntimeError('Google Cloud operation failed: '+' '.join(command[:3]))
        if not quiet and result.returncode==0:
            print('Completed: '+' '.join(command[:3]),flush=True)
        return result
    def exists(*command):return run(*command,check=False,quiet=True).returncode==0
    def account(name):return f'uptodate-{name}@{args.project}.iam.gserviceaccount.com'
    def project_role(member,role):
        run('projects','add-iam-policy-binding',args.project,'--member',member,'--role',role,'--condition=None',quiet=True)
    repository=f'{args.region}-docker.pkg.dev/{args.project}/uptodate/backend'
    if args.command=='prepare':
        run('services','enable','run.googleapis.com','cloudbuild.googleapis.com','artifactregistry.googleapis.com',
            'cloudtasks.googleapis.com','secretmanager.googleapis.com','cloudscheduler.googleapis.com',
            'iam.googleapis.com','iamcredentials.googleapis.com','logging.googleapis.com','storage.googleapis.com')
        for name in ['api','worker','tasks','build']:
            if not exists('iam','service-accounts','describe',account(name)):
                run('iam','service-accounts','create','uptodate-'+name,'--display-name','UpToDate '+name)
        if not exists('artifacts','repositories','describe','uptodate','--location',args.region):
            run('artifacts','repositories','create','uptodate','--repository-format=docker','--location',args.region)
        bucket='gs://'+args.project+'-uptodate-builds'
        if not exists('storage','buckets','describe',bucket):
            run('storage','buckets','create',bucket,'--location',args.region,'--uniform-bucket-level-access','--public-access-prevention')
        project_role('serviceAccount:'+account('build'),'roles/cloudbuild.builds.builder')
        project_role('serviceAccount:'+account('build'),'roles/logging.logWriter')
        if not exists('tasks','queues','describe','uptodate-generation','--location',args.region):
            run('tasks','queues','create','uptodate-generation','--location',args.region,
                '--max-concurrent-dispatches=2','--max-dispatches-per-second=2','--max-attempts=5','--min-backoff=10s','--max-backoff=300s')
        for name in ['api','worker']:
            run('tasks','queues','add-iam-policy-binding','uptodate-generation','--location',args.region,
                '--member','serviceAccount:'+account(name),'--role','roles/cloudtasks.enqueuer',quiet=True)
            run('iam','service-accounts','add-iam-policy-binding',account('tasks'),
                '--member','serviceAccount:'+account(name),'--role','roles/iam.serviceAccountUser',quiet=True)
        print('Infrastructure prepared; no always-on API or GPU instance provisioned.')
        return
    if args.command=='build':
        image=repository+':'+time.strftime('%Y%m%d-%H%M%S',time.gmtime())
        steps=[]
        build=['build','-t',image]
        if args.cache_image:
            if not args.cache_image.startswith(repository+':'):raise ValueError('Cache image must be from this backend repository')
            steps.append({'name':'gcr.io/cloud-builders/docker','args':['pull',args.cache_image]})
            build+=['--cache-from',args.cache_image]
        steps.append({'name':'gcr.io/cloud-builders/docker','args':[*build,'.']})
        config={'steps':steps,
                'images':[image],'serviceAccount':f'projects/{args.project}/serviceAccounts/{account("build")}',
                'options':{'logging':'CLOUD_LOGGING_ONLY'},'timeout':'1200s'}
        with tempfile.TemporaryDirectory(prefix='uptodate-build-') as directory:
            path=Path(directory)/'build.json'
            path.write_text(json.dumps(config))
            run('builds','submit','.', '--config',str(path),'--gcs-source-staging-dir',f'gs://{args.project}-uptodate-builds/source')
        print('BUILT_IMAGE='+image)
        return
    values=load_settings(ROOT/args.env_file)
    required=['DATABASE_URL','SUPABASE_URL','SUPABASE_PUBLISHABLE_KEY','SUPABASE_SECRET_KEY','MODEL_API_KEY']
    if any(not values.get(name) for name in required):
        raise RuntimeError('Save all required database, Supabase and Meta settings before deployment')
    if not args.image or not args.image.startswith(repository+':'):
        raise RuntimeError('Supply --image with the exact tag produced by the build command')
    secrets={}
    for name in [*required,'RESEND_API_KEY']:
        if not values.get(name):continue
        # URL and publishable key are public configuration, not secret resources.
        if name in ['SUPABASE_URL','SUPABASE_PUBLISHABLE_KEY']:continue
        identifier='uptodate-'+name.lower().replace('_','-')
        if not exists('secrets','describe',identifier):
            run('secrets','create',identifier,'--replication-policy=automatic',quiet=True)
        result=run('secrets','versions','add',identifier,'--data-file=-','--format=json',input=values[name],quiet=True)
        version=json.loads(result.stdout)['name'].rsplit('/',1)[-1]
        secrets[name]=identifier+':'+version
        for role in ['api','worker']:
            run('secrets','add-iam-policy-binding',identifier,'--member','serviceAccount:'+account(role),'--role','roles/secretmanager.secretAccessor',quiet=True)
    plain={
        'UPTODATE_ENV':'cloud','AUTH_MODE':'supabase','GOOGLE_CLOUD_PROJECT':args.project,
        'SUPABASE_URL':values['SUPABASE_URL'],'SUPABASE_PUBLISHABLE_KEY':values['SUPABASE_PUBLISHABLE_KEY'],
        'GENERATION_PROVIDER':'meta','META_MODEL':values.get('META_MODEL','muse-spark-1.3'),
        'META_REASONING_EFFORT':values.get('META_REASONING_EFFORT','low'),
        'IMAGE_GENERATION_PROVIDER':'meta','NEWSLETTER_SIGNUP_MODE':'browser',
        'POST_IMAGE_BUCKET':'post-images','CLOUD_TASKS_LOCATION':args.region,
        'CLOUD_TASKS_QUEUE':'uptodate-generation','CLOUD_TASKS_SERVICE_ACCOUNT':account('tasks'),
        'GENERATION_DAILY_BUDGET_USD':values.get('GENERATION_DAILY_BUDGET_USD','2'),
        'GENERATION_USER_DAILY_BUDGET_USD':values.get('GENERATION_USER_DAILY_BUDGET_USD','0.5'),
        'GENERATION_JOB_BUDGET_USD':values.get('GENERATION_JOB_BUDGET_USD','0.25'),
    }
    receiving_domain=values.get('RESEND_RECEIVING_DOMAIN') or values.get('RELAY_TEST_DOMAIN')
    if receiving_domain:plain['RESEND_RECEIVING_DOMAIN']=receiving_domain
    with tempfile.TemporaryDirectory(prefix='uptodate-run-') as directory:
        env_path=Path(directory)/'environment.json'
        def deploy(name,worker_url=''):
            settings={**plain,'CLOUD_RUN_ROLE':name,'CLOUD_WORKER_URL':worker_url}
            env_path.write_text(json.dumps(settings))
            env_path.chmod(0o600)
            command=['run','deploy','uptodate-'+name,'--image',args.image,'--region',args.region,
                     '--service-account',account(name),'--env-vars-file',str(env_path),
                     '--set-secrets',','.join(key+'='+value for key,value in secrets.items()),
                     '--cpu=1','--memory='+('2Gi' if name=='worker' else '512Mi'),
                     '--concurrency='+('1' if name=='worker' else '20'),
                     '--min-instances=0','--max-instances=2','--timeout='+('1800' if name=='worker' else '300'),
                     '--no-cpu-boost','--no-invoker-iam-check' if name=='api' else '--invoker-iam-check']
            run(*command)
            result=run('run','services','describe','uptodate-'+name,'--region',args.region,'--format=value(status.url)',quiet=True)
            return result.stdout.strip()
        worker=deploy('worker')
        # Bind the final service origin as OIDC audience and dispatch target.
        run('run','services','update','uptodate-worker','--region',args.region,'--update-env-vars','CLOUD_WORKER_URL='+worker)
        run('run','services','add-iam-policy-binding','uptodate-worker','--region',args.region,
            '--member','serviceAccount:'+account('tasks'),'--role','roles/run.invoker',quiet=True)
        api=deploy('api',worker)
        command=['scheduler','jobs','update','http','uptodate-maintenance'] if exists('scheduler','jobs','describe','uptodate-maintenance','--location',args.region) else ['scheduler','jobs','create','http','uptodate-maintenance']
        run(*command,'--location',args.region,'--schedule=*/5 * * * *','--time-zone=Etc/UTC',
            '--uri',worker+'/internal/maintenance','--http-method=POST','--attempt-deadline=300s',
            '--oidc-service-account-email',account('tasks'),'--oidc-token-audience',worker)
    print('API_URL='+api)
    print('WORKER_URL='+worker)
    print('Deployed with zero minimum instances. API/model credentials remain in Secret Manager.')


if __name__=='__main__':
    main()
