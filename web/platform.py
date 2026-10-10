"""Adapters over cv-cat/WeiboApis, with timeouts and normalized records."""
import json
import os
from pathlib import Path
import re
import time
from html import unescape
from urllib.parse import urlsplit,unquote
import requests
from bs4 import BeautifulSoup
from apis import weibo_apis, weibo_mobile_apis
from utils.weibo_utils import get_common_headers
from utils.weibo_creator_utils import get_post_image_headers
from .service import PlatformError, UncertainError


class BoundedRequests:
    """Module-local facade; never patch requests globally or retry writes."""
    @staticmethod
    def get(url,**kwargs):
        kwargs.setdefault('timeout',(8,20))
        response=requests.get(url,**kwargs)
        response.raise_for_status()
        return response

# Upstream read adapters have no timeout argument. Replace only their module binding.
weibo_apis.requests=BoundedRequests
weibo_mobile_apis.requests=BoundedRequests


def text(value): return unescape(re.sub('<[^>]+>','',str(value or '')))


def image(value):
    value=str(value or '')
    if value.startswith('//'): value='https:'+value
    return value if value.startswith('https://') else ''


def checked(result):
    success,_,raw=result
    if not success or not isinstance(raw,dict) or raw.get('ok',1) not in (1,True):
        raise PlatformError('微博接口未确认请求，请检查登录、网络或稍后重试')
    return raw


def projection(raw):
    user=raw.get('user') or {}; item_id=str(raw.get('idstr') or raw.get('id') or raw.get('mid') or '')
    pictures=raw.get('pics') or []
    cover=((pictures[0].get('large') or {}).get('url') or pictures[0].get('url')) if pictures else ''
    content=text(raw.get('text_raw') or raw.get('text'))
    return {'id':item_id,'title':content[:90],'text':content,'author':text(user.get('screen_name') or user.get('name')),
        'uid':str(user.get('id','')),'cover':image(cover),'url':'https://m.weibo.cn/detail/'+item_id,
        'views':raw.get('reposts_count',0),'likes':raw.get('attitudes_count',0),'comments':raw.get('comments_count',0),
        'created':raw.get('created_at',''),'tags':'','note':''}


def cards(values):
    result=[]
    for card in values:
        if card.get('mblog'): result.append(projection(card['mblog']))
        if card.get('card_group'): result.extend(cards(card['card_group']))
    return result


class Platform:
    def __init__(self,directory):
        self.path=Path(directory)/'account.json'
        self.web=weibo_apis.WeiboApis(); self.mobile=weibo_mobile_apis.WeiboMobileApis()

    def identity(self):
        return json.loads(self.path.read_text())['account'] if self.path.exists() else None

    def cookie(self):
        if not self.path.exists(): raise PlatformError('请先导入微博登录 Cookie')
        return json.loads(self.path.read_text())['cookie']

    def persist(self,cookie,account):
        tmp=self.path.with_suffix('.tmp'); fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as out: json.dump({'cookie':cookie,'account':account},out,ensure_ascii=False)
        os.replace(tmp,self.path); return account

    def connect(self,cookie):
        cookies=dict(part.strip().split('=',1) for part in cookie.split(';') if '=' in part)
        if not cookies.get('SUB') or not cookies.get('XSRF-TOKEN'): raise PlatformError('Cookie 需要包含 SUB 和 XSRF-TOKEN，请在 weibo.com 登录后复制')
        cookie='; '.join(f'{key}={value}' for key,value in cookies.items())
        try: uid,name=self.web.get_self_info(cookie)
        except Exception: raise PlatformError('无法确认微博账号，Cookie 可能已过期，请在 weibo.com 重新复制')
        if not str(uid).isdigit(): raise PlatformError('没有读取到有效微博 UID')
        return self.persist(cookie,{'id':str(uid),'name':text(name),'status':'connected','checked_at':time.time()})

    def check(self): return self.connect(self.cookie())

    def disconnect(self): self.path.unlink(missing_ok=True)

    def search(self,keyword,page,order):
        try:
            raw=checked(self.mobile.searchSome(keyword,page))
            items=cards((raw.get('data') or {}).get('cards') or [])
        except PlatformError:
            if not self.identity():raise PlatformError('微博移动端搜索被限制，请先连接账号后使用网页登录态搜索')
            cookie=self.cookie();cookies=dict(part.strip().split('=',1) for part in cookie.split(';') if '=' in part)
            response=BoundedRequests.get('https://s.weibo.com/weibo',params={'q':keyword,'page':page},headers=get_common_headers(cookies),cookies=cookies)
            soup=BeautifulSoup(response.text,'html.parser');items=[]
            for node in soup.select('.card-wrap[mid]'):
                item_id=node.get('mid','');body=node.select_one('.txt[node-type="feed_list_content_full"]') or node.select_one('.txt')
                author=node.select_one('.name');link=author.get('href','') if author else '';match=re.search(r'/(?:u/)?(\d{5,24})(?:[/?]|$)',link)
                content=body.get_text(' ',strip=True) if body else ''
                if not item_id or not content:continue
                item=projection({'id':item_id,'text':content,'user':{'screen_name':author.get_text(strip=True) if author else '', 'id':match.group(1) if match else ''}})
                pic=node.select_one('.media-piclist img')
                if pic:item['cover']=image(pic.get('src',''))
                items.append(item)
            if not items and not soup.select_one('.card-no-result,.m-error'):
                raise PlatformError('微博搜索未返回可识别的结果，请在官网检查登录或验证状态')
        return {'items':items,'has_more':bool(items),'total':len(items)}

    @staticmethod
    def resolve(value):
        if re.fullmatch(r'\d{6,24}',value): return value
        parsed=urlsplit(value)
        if parsed.scheme!='https' or parsed.hostname not in ('m.weibo.cn','weibo.com','www.weibo.com') or parsed.port not in (None,443) or parsed.username or parsed.password:
            raise PlatformError('请输入微博数字 ID 或微博详情链接')
        match=re.fullmatch(r'/(?:detail|status)/(\d{6,24})/?',parsed.path)
        if match: return match.group(1)
        # PC URLs use base62 IDs. The mobile detail endpoint accepts these too.
        match=re.fullmatch(r'/\d{5,24}/([A-Za-z0-9]{5,16})/?',parsed.path)
        if match: return match.group(1)
        raise PlatformError('链接中没有有效微博 ID')

    def detail(self,value):
        item_id=self.resolve(value)
        try:
            raw=checked(self.mobile.getWorkInfo(item_id))
            status=raw.get('status') or (raw.get('data') or {}).get('status')
            if not status:raise PlatformError('未读取到博文')
        except PlatformError:
            if not self.identity():raise PlatformError('移动端详情读取失败，请连接微博账号后重试')
            cookies=dict(part.strip().split('=',1) for part in self.cookie().split(';') if '=' in part)
            response=BoundedRequests.get('https://weibo.com/ajax/statuses/show',params={'id':item_id},headers=get_common_headers(cookies),cookies=cookies)
            status=response.json()
            if not status.get('id') or status.get('ok',1)!=1:raise PlatformError('未读取到博文，可能已删除或需要验证')
        return projection(status)

    def comments(self,item):
        raw=checked(self.web.getWordComments(item['uid'],item['id'],self.cookie()))
        return {'comments':[{'id':str(row.get('id','')),'author':text((row.get('user') or {}).get('screen_name')),
            'text':text(row.get('text')),'likes':row.get('like_counts',0)} for row in raw.get('data') or []],
            'notice':'展示当前页评论'}

    def user(self,uid,page):
        raw=checked(self.web.getUserPosted(uid,str(page),'',self.cookie())); data=raw.get('data') or {}
        items=[projection(row) for row in data.get('list') or []]
        return {'items':items,'page':page,'has_more':bool(items)}

    def publish(self,content):
        cookie=self.cookie(); cookies=dict(part.strip().split('=',1) for part in cookie.split(';') if '=' in part)
        # Mirrors the upstream text/image branch, which refers to an undefined global instance.
        try:
            response=requests.post('https://weibo.com/ajax/statuses/update',headers=get_post_image_headers(unquote(cookies['XSRF-TOKEN'])),
                cookies=cookies,data={'content':content,'visible':'0','vote':'','media':'','pic_id':'[]'},timeout=(8,25),allow_redirects=False)
            raw=response.json()
            if raw.get('ok')!=1: raise UncertainError()
            data=raw.get('data') or {}; item_id=str(data.get('idstr') or data.get('id') or '')
            if not item_id: raise UncertainError()
            return {'id':item_id,'url':'https://m.weibo.cn/detail/'+item_id}
        except Exception: raise UncertainError()

    def qr_start(self): raise PlatformError('此版本微博接口不支持扫码登录，请导入 Cookie')
    def qr_poll(self,token): raise PlatformError('请使用 Cookie 连接微博账号')
