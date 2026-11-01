"""Direct AI editions enter the existing source engine without an email envelope."""
import hashlib
import re
from typing import Literal

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

from .documents import Document, make_spans, normalize, parse_document
from .locations import email_places, map_link
from .retrieval import Resource, SourceError, public_url


class SourceLink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=8, max_length=3000)
    title: str = Field(default='', max_length=200)

    @field_validator('url')
    @classmethod
    def validate_url(cls, value):
        try:return public_url(value)
        except SourceError as error:raise ValueError(str(error)) from None


class StoryImage(SourceLink):
    caption: str = Field(default='', max_length=500)


class ConnectorStory(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default='', max_length=50000)
    content_format: Literal['text','markdown','html'] = 'markdown'
    source_url: str | None = Field(default=None, max_length=3000)
    links: list[SourceLink] = Field(default_factory=list, max_length=30)
    images: list[StoryImage] = Field(default_factory=list, max_length=8)

    @field_validator('source_url')
    @classmethod
    def validate_url(cls, value):
        if value is None:return None
        return SourceLink.validate_url(value)

    @model_validator(mode='after')
    def usable(self):
        if not self.title.strip() or not (self.content.strip() or self.source_url):
            raise ValueError('Each story needs a title and content or an original source URL')
        return self


class ConnectorEdition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    newsletter_key: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,79}$')
    newsletter_name: str = Field(min_length=1, max_length=100)
    edition_id: str = Field(min_length=1, max_length=160)
    source_id: str | None = Field(default=None, max_length=64)
    stories: list[ConnectorStory] = Field(min_length=1, max_length=12)

    @field_validator('newsletter_name','edition_id')
    @classmethod
    def nonempty(cls,value):
        if not value.strip():raise ValueError('This value cannot be blank')
        return value.strip()


def connector_documents(payload):
    edition = ConnectorEdition.model_validate(payload['edition'])
    identifier = 'connector:' + payload['fingerprint']
    documents=[]
    for index, story in enumerate(edition.stories):
        url = identifier + '#' + str(index)
        if story.content_format == 'html':
            document=parse_document(Resource(url,'text/html',story.content.encode()))
            plain=BeautifulSoup(story.content,'html.parser').get_text('\n',strip=True)
        else:
            plain=story.content
            chunks=[];section='Body'
            for line in plain.splitlines():
                text=normalize(line)
                if not text:continue
                if text.startswith('#'):
                    section=text.lstrip('# ').rstrip(':')
                    continue
                chunks.append((text,section,None))
            document=Document(url,story.title,make_spans(chunks))
            # Markdown anchors preserve their labels, which helps location and
            # paper/repository actions without another extraction model call.
            for label, target in re.findall(r'(?<!!)\[([^\]]+)\]\((https?://[^\s)]+)\)',plain):
                document.links.append({'url':target,'title':label})
            for label,target in re.findall(r'!\[([^\]]*)\]\((https?://[^\s)]+)\)',plain):
                document.figures.append({'id':'inline-'+str(len(document.figures)), 'url':target,
                    'caption':label,'license_url':'','page':None,'selection_basis':'connector_image'})
        document.title=story.title
        document.links += [link.model_dump() for link in story.links]
        if story.source_url:document.links.insert(0,{'url':story.source_url,'title':'Original source'})
        known={link['url'] for link in document.links}
        for candidate in re.findall(r'https?://[^\s<>\]"\)]+',plain):
            candidate=candidate.rstrip('.,;')
            if candidate not in known:
                document.links.append({'url':candidate,'title':candidate});known.add(candidate)
        document.figures += [{'id':'connector-'+str(i),'url':item.url,
            'caption':item.caption or item.title,'license_url':'','page':None,
            'selection_basis':'connector_image'} for i,item in enumerate(story.images)]
        document.metadata.update(private=True,format='connector',author='Muse',
            source_text=plain,connector_subject=story.title,connector_primary_url=story.source_url)
        # This parser also handles normal text addresses and inline Maps links;
        # the function name reflects its earlier email input, not an envelope.
        places=email_places(document)
        if places:
            document.metadata['places']=places
            if story.source_url:document.url=story.source_url
        document.metadata['location_links']=[link for link in document.links if map_link(link.get('url',''))]
        document.metadata['_links']=document.links
        document.metadata['_figures']=document.figures
        document.metadata['source_content_hash']=hashlib.sha256((story.title+'\n'+plain).encode()).hexdigest()
        documents.append(document)
    root=Document(identifier,edition.newsletter_name,
        make_spans([(document.title+'\n'+'\n'.join(span['text'] for span in document.spans),'Edition',None) for document in documents]),
        metadata={'private':True,'format':'connector','author':'Muse'})
    return root,documents
