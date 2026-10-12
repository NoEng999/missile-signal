from pathlib import Path
import requests, zipfile, tarfile, io
ROOT=Path(__file__).resolve().parent
dest=ROOT/'tools'; dest.mkdir(exist_ok=True)
def download(url):
    r=requests.get(url,timeout=180); r.raise_for_status(); return r.content
if not (dest/'signal-cli-0.14.9').exists():
    print('Downloading signal-cli 0.14.9',flush=True)
    blob=download('https://github.com/AsamK/signal-cli/releases/download/v0.14.9/signal-cli-0.14.9.tar.gz')
    with tarfile.open(fileobj=io.BytesIO(blob),mode='r:gz') as t:
        for member in t.getmembers():
            target=(dest/member.name).resolve()
            if not target.is_relative_to(dest.resolve()) or member.issym() or member.islnk():
                raise ValueError('Unsafe archive member')
        t.extractall(dest)
if not list(dest.glob('jdk-*/bin/java.exe')):
    print('Downloading portable Java 25 from Eclipse Adoptium',flush=True)
    assets=requests.get('https://api.adoptium.net/v3/assets/latest/25/hotspot',params={'architecture':'x64','image_type':'jre','os':'windows'},timeout=30).json()
    url=assets[0]['binary']['package']['link']
    blob=download(url)
    with zipfile.ZipFile(io.BytesIO(blob)) as z: z.extractall(dest)
print('Signal runtime ready',flush=True)
