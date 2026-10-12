"""Read only the NHK article body, never the surrounding news lists."""
import json
from bs4 import BeautifulSoup

def parse_nhk(html):
    soup=BeautifulSoup(html,'html.parser')
    metadata={}
    def value(v): return v.get('@value','') if isinstance(v,dict) else (v or '')
    for script in soup.find_all('script',type='application/ld+json'):
        try: obj=json.loads(script.string or script.get_text())
        except (ValueError,TypeError): continue
        objects=obj if isinstance(obj,list) else obj.get('@graph',[obj])
        for item in objects:
            if isinstance(item,dict) and item.get('@type')=='NewsArticle': metadata=item; break
    # Current NHK ONE body component. Related cards use different components.
    node=soup.find(attrs={'itemprop':'articleBody'}) or soup.find('div',class_='_1i1d7sh0')
    if node:
        for child in node.find_all(['script','style','nav','aside','footer']): child.decompose()
        paragraphs=[p.get_text(' ',strip=True) for p in node.find_all(['p','h2','h3']) if not p.find_parent(['aside','nav'])]
        body='\n'.join(dict.fromkeys(p for p in paragraphs if p))
    else:
        body=value(metadata.get('articleBody')) or value(metadata.get('description'))
    if not body: raise ValueError('NHKの本文を特定できません（ページ全体は通知しません）')
    return body, metadata.get('dateModified') or metadata.get('datePublished') or ''
