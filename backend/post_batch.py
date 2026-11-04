"""Owner-scoped batch lookups for post collections; serialization stays shared."""
import json
from urllib.parse import urlsplit


def chunks(values, size=200):
    values=list(dict.fromkeys(value for value in values if value is not None))
    for start in range(0,len(values),size):
        yield values[start:start+size]


class PostBatch:
    def __init__(self,db,rows,user):
        self.sources,self.artifact_revisions,self.documents,self.references={},{},{},{}
        self.bookmarked,self.read=set(),set()
        owner=user['id']
        for ids in chunks(row['source_id'] for row in rows):
            marks=','.join('?' for _ in ids)
            for source in db.execute('SELECT * FROM sources WHERE user_id=? AND id IN ('+marks+')',[owner,*ids]):
                self.sources[source['id']]=source
        for ids in chunks(row['id'] for row in rows):
            marks=','.join('?' for _ in ids)
            for item in db.execute("SELECT post_id,'bookmark' AS kind FROM bookmarks WHERE user_id=? AND post_id IN ("+marks+
                    ") UNION ALL SELECT post_id,'read' AS kind FROM reads WHERE user_id=? AND post_id IN ("+marks+')',
                    [owner,*ids,owner,*ids]):
                (self.bookmarked if item['kind']=='bookmark' else self.read).add(item['post_id'])
            for item in db.execute('SELECT ps.post_id,s.id,s.name,s.url FROM post_sources ps JOIN sources s ON s.id=ps.source_id '
                    'WHERE ps.user_id=? AND s.user_id=? AND ps.post_id IN ('+marks+')',[owner,owner,*ids]):
                self.references.setdefault(item['post_id'],[]).append({key:item[key] for key in ('id','name','url')})
        jobs,artifacts=set(),set()
        for row in rows:
            analysis=json.loads(row['analysis']);paper=analysis.get('source') or {}
            missing_abstract=not paper.get('abstract') and (analysis.get('content_type')=='paper' or
                urlsplit(row['original_url']).hostname in ('arxiv.org','www.arxiv.org') or paper.get('doi'))
            if missing_abstract and not paper.get('revision_hash') and row['artifact_key']:
                artifacts.add(row['artifact_key'])
            if missing_abstract or analysis.get('link_actions') is None:jobs.add(row['job_id'])
        for ids in chunks(artifacts):
            marks=','.join('?' for _ in ids)
            for item in db.execute('SELECT cache_key,content_hash FROM generation_artifacts WHERE cache_key IN ('+marks+')',ids):
                self.artifact_revisions[item['cache_key']]=item['content_hash']
        for ids in chunks(jobs):
            marks=','.join('?' for _ in ids)
            for item in db.execute('SELECT job_id,url,title,metadata,content_hash,spans FROM documents '
                    'WHERE user_id=? AND job_id IN ('+marks+') ORDER BY created_at ASC',[owner,*ids]):
                self.documents.setdefault((item['job_id'],item['url']),[]).append(item)

    def saved_document(self,row,revision=None):
        return next((item for item in self.documents.get((row['job_id'],row['original_url']),[])
            if not revision or item['content_hash']==revision),None)
