"""Small, publisher-verified starter catalog; ranking makes no paid model calls."""
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from typing import Literal

Interest = Literal['ai','research','cooking','coffee','business','travel']

TOPICS = [
    {'id':'ai','name':'AI & tech','symbol':'cpu'},
    {'id':'research','name':'Research','symbol':'atom'},
    {'id':'cooking','name':'Cooking','symbol':'fork.knife'},
    {'id':'coffee','name':'Coffee & dining','symbol':'cup.and.saucer'},
    {'id':'business','name':'Business','symbol':'chart.line.uptrend.xyaxis'},
    {'id':'travel','name':'Travel','symbol':'airplane'},
]

CATALOG = [
    {'id':'alphasignal','name':'AlphaSignal','description':'AI developments, research, and tools.','url':'https://newsletter.alphasignal.ai/','interests':['ai','research']},
    {'id':'tldr_ai','name':'TLDR AI','description':'A concise digest of AI news, papers, and developer tools.','url':'https://tldr.tech/ai','interests':['ai','research']},
    {'id':'the_batch','name':'The Batch','description':'AI news and research explained by DeepLearning.AI.','url':'https://www.deeplearning.ai/the-batch/','interests':['ai','research']},
    {'id':'nature_briefing','name':'Nature Briefing: AI & Robotics','description':'How AI and robotics are changing science.','url':'https://www.nature.com/briefing/ai-robotics','interests':['research','ai']},
    {'id':'smitten_kitchen','name':'Smitten Kitchen','description':'Seasonal meal ideas and recipes for home cooks.','url':'https://smittenkitchen.com/subscribe/','interests':['cooking']},
    {'id':'eater','name':'Eater','description':'Restaurant news, dining guides, and food culture.','url':'https://www.eater.com/newsletters','interests':['coffee','cooking','travel']},
    {'id':'sprudge','name':'The Sprudge Report','description':'Coffee stories, cafés, and specialty-coffee culture.','url':'https://sprudge.substack.com/','interests':['coffee']},
    {'id':'perfect_daily_grind','name':'Perfect Daily Grind','description':'Coffee-industry news and stories from across the sector.','url':'https://perfectdailygrind.com/','interests':['coffee']},
    {'id':'the_hustle','name':'The Hustle','description':'Business stories, emerging ideas, and unusual startups.','url':'https://thehustle.co/join','interests':['business']},
    {'id':'1440','name':'1440','description':'A broad daily digest of news and ideas.','url':'https://join1440.com/','interests':['business','research']},
    {'id':'afar','name':'AFAR','description':'Travel inspiration and ideas for your next trip.','url':'https://www.afar.com/newsletters','interests':['travel']},
    {'id':'atlas_obscura','name':'Atlas Obscura','description':'Unusual places, travel stories, and curious discoveries.','url':'https://www.atlasobscura.com/newsletters','interests':['travel']},
]


class PreferencesInput(BaseModel):
    interests: list[Interest] = Field(default_factory=list, max_length=6)
    complete: bool = True


def preferences(metadata):
    raw=metadata.get('uptodate_preferences') or {}
    if not isinstance(raw,dict):raw={}
    ids={item['id'] for item in TOPICS}
    selected=raw.get('interests',[])
    selected=list(dict.fromkeys(value for value in selected if isinstance(value,str) and value in ids))[:6] if isinstance(selected,list) else []
    return {'interests':selected,'complete':raw.get('complete') is True}


def url_identity(value):
    parts=urlsplit(value or '')
    return (parts.hostname or '').removeprefix('www.').lower(),parts.path.rstrip('/')


def recommendations(selected, sources=()):
    selected=list(dict.fromkeys(value for value in selected if value in {topic['id'] for topic in TOPICS}))
    saved={url_identity(source.get('signup_url') or source.get('url')) for source in sources if not source.get('archived_at') and source.get('subscription_status') not in ('unsubscribed','unsubscribe_pending','unsubscribing')}
    candidates=[item for item in CATALOG if set(item['interests']) & set(selected)]
    # Round-robin across interests so the first topic cannot crowd out the rest.
    ordered=[]
    for index in range(len(candidates)):
        for topic in selected:
            matches=[item for item in candidates if topic in item['interests']]
            if index<len(matches) and matches[index] not in ordered:ordered.append(matches[index])
        if len(ordered)>=5:break
    return [{**item,'matched_interests':[topic for topic in selected if topic in item['interests']],
             'already_added':url_identity(item['url']) in saved} for item in ordered[:5]]
