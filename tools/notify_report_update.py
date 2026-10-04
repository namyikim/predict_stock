"""게시된 개장 전 예측·마감 회고를 요약하여 구독 Worker의 발송 대기에 넣는다."""
import hashlib
import hmac
import time
import argparse
import csv
import io
import html
import re
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
REPO = 'namyikim/predict_stock'
KST = timezone(timedelta(hours=9))
NAMES = {'samsung':'삼성전자', 'sk_hynix':'SK하이닉스'}


def instant(value):
    dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if dt.tzinfo is None:
        raise ValueError('시각에 시간대가 없습니다')
    return dt


def number(value, percent=False):
    try:
        n=float(value)
        if not math.isfinite(n):
            raise ValueError()
        return f'{n*100:+.2f}%' if percent else f'{n:,.0f}원'
    except (ValueError,TypeError):
        return '미확인'


def base(target, phase, session, lines):
    return {'target':target,'phase':phase,'session_date':session,'lines':lines,
            'url':f'https://namyikim.github.io/predict_stock/{target}/'}


def build_pre_open(target, rows, now):
    today=now.astimezone(KST).date().isoformat()
    eligible=[]
    for r in rows:
        if r.get('kind')!='direction' or r.get('model')!='No macro ensemble' or r.get('horizon_days')!='1':
            continue
        try:
            made=instant(r['created_at_utc'])
            opening=datetime.fromisoformat((r.get('prediction_date') or r['target_date'])+'T09:00:00+09:00')
        except (KeyError,ValueError):
            continue
        if (today <= r['target_date'] and made <= now and made < opening and
                str(r.get('is_prospective','')).lower()=='true' and r.get('information_cutoff','pre_open') in ('','pre_open')):
            eligible.append((r,made))
    if not eligible:
        return None
    day=max(r['target_date'] for r,_ in eligible)
    # 공식 원장처럼 당일 아침을 우선하고 그 중 최초 기록을 고른다.
    candidates=[(r,t) for r,t in eligible if r['target_date']==day]
    r,_=min(candidates,key=lambda x:(x[1].astimezone(KST).date().isoformat()!=day,x[1]))
    probs=[]
    for key,label in [('p_up','상승'),('p_flat','보합'),('p_down','하락')]:
        try:
            value=float(r.get(key,''))
            if not math.isfinite(value) or not 0<=value<=1:
                return None
        except (ValueError,TypeError):
            return None
        probs.append(f'{label} {value*100:.1f}%')
    lines=[f"오늘의 장 예측: {r.get('prediction','미확인')} (대표 모델: 시세만)", ' · '.join(probs),
           '예측 기록 시각: '+instant(r['created_at_utc']).astimezone(KST).strftime('%Y-%m-%d %H:%M KST')]
    for kind,field,label in [('open','predicted_open','예상 시초가'),('price','predicted_close','예상 종가')]:
        price=next((x for x in rows if x.get('run_id')==r.get('run_id') and x.get('target_date')==day and x.get('kind')==kind and x.get('model')=='Ridge' and x.get('horizon_days')=='1'),None)
        if price:
            lines.append(label+': '+number(price.get(field)))
    return dict(base(target,'pre_open',day,lines),report_run_id=r.get('run_id'))


def build_post_close(target, review):
    s=review.get('summary',{})
    lines=['종가 '+number(s.get('close'))+' · 전일 대비 '+number(s.get('c2c'),True),
           '시초가 갭 '+number(s.get('gap'),True)+' · 장중 변화 '+number(s.get('session'),True)]
    labels=review.get('classification',{}).get('labels',[])
    if labels:
        lines.append('장 흐름: '+' · '.join(labels))
    lines.append('수급: '+str(review.get('flows') or '자료 미확인'))
    status=review.get('flow_status',{})
    if status.get('state') not in (None,'ok'):
        lines.append('수급 자료 상태: '+str(status.get('state')))
    for f in review.get('forecasts',[]):
        if f.get('model')=='No macro ensemble':
            lines.append('아침 예측 검증: '+str(f.get('verdict','미확인')))
            break
    if review.get('price_check'):
        lines.append(str(review['price_check']))
    lines.append('회고는 관측 내용을 설명하며 원인을 확정하지 않습니다.')
    return dict(base(target,'post_close',review['session_date'],lines),review_generated_at=review.get('generated_at'))


def publication_matches(payload, page):
    if payload['phase']=='pre_open':
        run_id=payload.get('report_run_id')
        return bool(run_id and payload['session_date'] in page and 'run_id <code>'+html.escape(run_id)+'</code>' in page)
    made=payload.get('review_generated_at')
    return bool(made and payload['session_date'] in page and html.escape(str(made)) in page)


def read_review(read, target, session):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',session):
        raise ValueError('회고 날짜 형식이 다릅니다')
    try:
        review=json.loads(read(f'forecast_history/{target}/reviews/{session}.json'))
    except (FileNotFoundError,HTTPError) as exc:
        if isinstance(exc,FileNotFoundError) or exc.code==404:
            return None
        raise
    if review.get('session_date')!=session:
        raise ValueError('회고 대상일이 다릅니다')
    return review


def download(path, revision):
    with urlopen(f'https://raw.githubusercontent.com/{REPO}/{revision}/{path}',timeout=30) as response:
        return response.read().decode('utf-8-sig')


def automatic_run(env, key, phase, now=None):
    """예약 실행 또는 서명된 Cron 호출의 첫 시도만 허용한다."""
    if env.get('GITHUB_ACTIONS') != 'true' or env.get('GITHUB_RUN_ATTEMPT') != '1':
        return False
    event=env.get('GITHUB_EVENT_NAME')
    if event=='schedule':
        return True
    if event!='workflow_dispatch' or env.get('MAIL_CALLER')!='cloudflare-cron' or not key:
        return False
    parts=env.get('MAIL_AUTOMATION_PROOF','').split(':')
    if len(parts)!=3 or not re.fullmatch(r'[0-9]+',parts[0]) or not re.fullmatch(r'[a-f0-9]{64}',parts[2]):
        return False
    age=(time.time() if now is None else now)-int(parts[0])
    if not 0<=age<=4*3600:
        return False
    workflow='daily-report.yml' if phase=='pre_open' else 'afternoon-report.yml'
    expected=hmac.new(key.encode(),(workflow+'|'+':'.join(parts[:2])).encode(),hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected,parts[2])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',choices=NAMES,required=True)
    parser.add_argument('--phase',choices=['pre_open','post_close'],required=True)
    parser.add_argument('--session')
    parser.add_argument('--preview',action='store_true',help='로컬 자료의 내용만 출력. 전송하지 않음')
    args=parser.parse_args()
    endpoint=os.environ.get('REPORT_MAIL_ENDPOINT','').rstrip('/')
    token=os.environ.get('MAIL_PUBLISH_TOKEN','')
    if not args.preview and not automatic_run(os.environ,token,args.phase):
        print('수동 실행·재실행은 이메일을 발송하지 않습니다.')
        return
    if not args.preview and (not endpoint or not token):
        print('이메일 발송 연동 미설정: REPORT_MAIL_ENDPOINT / MAIL_PUBLISH_TOKEN 필요')
        return
    if args.preview:
        revision='0'*40
        read=lambda p:(ROOT/p).read_text(encoding='utf-8-sig')
    else:
        if not endpoint.startswith('https://') or '?' in endpoint or '#' in endpoint:
            raise ValueError('Worker의 HTTPS 주소를 설정하세요')
        headers={'User-Agent':'predict-stock-report-email','Accept':'application/vnd.github+json'}
        if os.environ.get('GITHUB_TOKEN'):
            headers['Authorization']='Bearer '+os.environ['GITHUB_TOKEN']
        with urlopen(Request(f'https://api.github.com/repos/{REPO}/commits/main',headers=headers),timeout=30) as response:
            revision=json.load(response)['sha']
        read=lambda p:download(p,revision)
    now=datetime.now(timezone.utc)
    if args.phase=='pre_open':
        rows=list(csv.DictReader(io.StringIO(read(f'forecast_history/{args.target}/forecast_log.csv'))))
        payload=build_pre_open(args.target,rows,now)
    else:
        if not args.session:
            parser.error('마감 회고는 --session 날짜가 필요합니다')
        review=read_review(read,args.target,args.session)
        if review is None:
            print('게시된 해당 날짜 회고 없음: 이메일 알림 건너뜀')
            return
        payload=build_post_close(args.target,review)
    if not payload:
        print('발송할 새 개장 전 예측 없음')
        return
    payload['source_revision']=revision
    if args.preview:
        print(json.dumps(payload,ensure_ascii=False,indent=2))
        return
    payload['automation']={'event':os.environ['GITHUB_EVENT_NAME'],'attempt':1,
                           'caller':os.environ.get('MAIL_CALLER',''),
                           'proof':os.environ.get('MAIL_AUTOMATION_PROOF','')}
    page=read(f'docs/{args.target}/index.html')
    if not publication_matches(payload,page):
        raise RuntimeError('보고서 HTML과 알림 원장의 실행/생성 시각이 다릅니다. 게시 완료 후 다시 실행하세요.')
    request=Request(endpoint+'/mail/events',data=json.dumps(payload).encode(),method='POST',
                    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urlopen(request,timeout=30) as response:
        result=json.load(response)
    print('이메일 발송 대기 등록:',result.get('status'),result.get('event_id',''))
    if not result.get('enabled') or not result.get('configured'):
        print('::warning::Worker 이메일 발송이 비활성화되어 있거나 발송 설정이 미완료입니다.')


if __name__=='__main__':
    main()
