"""Trusted default-branch controller. Never checks out or executes PR code.

Only API metadata enters policy evaluation. Provider fixes are separate stacked
candidates; they do not approve, merge, or waive checkpoint validation.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import re
import subprocess

REPO = 'bbukolla-eng/Heleos-spark'
OWNER = 'bbukolla-eng'
REVIEWERS = {'coderabbitai[bot]', 'chatgpt-codex-connector[bot]'}
SENSITIVE = ('.github/', '.claude/', '.githooks/', '.agents/', 'tools/github/',
             'tools/egress/', 'tools/ci/', 'docs/policies/', 'docs/decisions/', 'docs/architecture/decisions/', 'docs/roadmap/',
             'crates/heleos-worker-', 'docs/operations/github-automation-')
SENSITIVE_FILES = {'AGENTS.md','CLAUDE.md','CURSOR.md','GROK.md','GROKBOTS.md',
                   'SKILLS.md','.coderabbit.yaml','SECURITY.md','GOAL.md','ROADMAP.md','docs/roadmap.md',
                   'docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md',
                   'scripts/verify-build-checkpoint.py','scripts/install-build-hooks.py',
                   'scripts/active-build-status.py'}


def gh(endpoint, data=None, method=None):
    cmd = ['gh','api',endpoint]
    if method: cmd += ['--method',method]
    if data is not None: cmd += ['--input','-']
    p = subprocess.run(cmd,input=json.dumps(data) if data is not None else None,
                       text=True,capture_output=True,check=True)
    return json.loads(p.stdout) if p.stdout.strip() else None


def pages(endpoint, key=None):
    items=[]
    for page in range(1,101):
        result=gh(endpoint+('&' if '?' in endpoint else '?')+f'per_page=100&page={page}')
        batch=result[key] if key else result
        if not isinstance(batch,list): raise ValueError('Malformed paginated data')
        items.extend(batch)
        if len(batch)<100: return items
    raise ValueError('Pagination limit exceeded; refusing partial evidence')


def stamp(value):
    result = dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None: raise ValueError('Timestamp must include timezone')
    return result


def independent_approvals(reviews, head, writers):
    latest={}
    for review in sorted(reviews,key=lambda r:r['id']):
        login=review.get('user',{}).get('login','')
        if review.get('state') in {'APPROVED','CHANGES_REQUESTED','DISMISSED'}: latest[login]=review
    if any(r.get('state')=='CHANGES_REQUESTED' for r in latest.values()): return set()
    return {login for login,r in latest.items() if login in REVIEWERS and login not in writers
            and r.get('user',{}).get('type')=='Bot' and r.get('state')=='APPROVED'
            and r.get('commit_id')==head}


def evaluate(pr, files, reviews, comments, checks, writers, base, activation, app_login, security_protected=False):
    reasons=[];head=pr['head']['sha']
    if not re.fullmatch('[0-9a-f]{40}',head): reasons.append('invalid head')
    if pr.get('state')!='open' or pr.get('draft'): reasons.append('not a ready open PR')
    if pr['base']['ref']!='main' or pr['base']['sha']!=base: reasons.append('target changed')
    if pr['head'].get('repo',{}).get('full_name')!=REPO: reasons.append('fork')
    if pr.get('mergeable') is not True or pr.get('mergeable_state')!='clean': reasons.append('GitHub merge requirements pending')
    if not activation or stamp(pr['created_at'])<stamp(activation): reasons.append('predates activation')
    if 'heleos-delivery' not in {l['name'] for l in pr.get('labels',[])}: reasons.append('not enrolled for automatic delivery')
    if 'automation-hold' in {l['name'] for l in pr.get('labels',[])}: reasons.append('owner hold')
    if len(files)!=pr['changed_files'] or not files: reasons.append('incomplete file inventory')
    sensitive=any(p in SENSITIVE_FILES or p.startswith(SENSITIVE) for f in files
                  for p in [f['filename'],f.get('previous_filename',f['filename'])])
    consent=f'Heleos owner consent {head}'
    if sensitive and not any(c.get('user',{}).get('login')==OWNER and c.get('body','').strip()==consent for c in comments):
        reasons.append('sensitive change requires owner consent for this head')
    if not independent_approvals(reviews,head,writers|{pr['user']['login'],app_login}): reasons.append('independent current-head approval missing')
    for name in ['checks','dependency-review']:
        runs=[c for c in checks if c.get('name')==name and c.get('head_sha')==head and c.get('app',{}).get('id')==15368]
        if not runs or max(runs,key=lambda c:c['id']).get('conclusion')!='success': reasons.append(name+' missing/failed')
    # The verified native CodeQL rule and clean main-target mergeability are
    # authoritative; do not invent a separate scanner app/status publisher.
    if not security_protected: reasons.append('native CodeQL protection not verified')
    return {'eligible':not reasons,'reasons':reasons,'sensitive':sensitive,'head':head}



def refresh_candidate(url, number, expected_head, files, writers, base, activation, app_login, security_protected=False):
    """Reload revocable approval, consent, checks and PR state before acting."""
    fresh=gh(url)
    if fresh['head']['sha']!=expected_head or fresh['base']['sha']!=base:
        return fresh, {'eligible':False,'reasons':['candidate or target changed'],
                       'head':fresh['head']['sha']}, [], []
    reviews=pages(url+'/reviews')
    comments=pages(f'repos/{REPO}/issues/{number}/comments')
    checks=pages(f'repos/{REPO}/commits/{expected_head}/check-runs','check_runs')
    result=evaluate(fresh,files,reviews,comments,checks,writers,base,activation,
                    app_login,security_protected=security_protected)
    return fresh,result,reviews,comments


def fix_request(pr, files, reviews, comments, activation, app_login):
    head=pr['head']['sha'];marker=f'<!-- heleos-autofix:{head} -->'
    if not activation or stamp(pr['created_at'])<stamp(activation): return None
    if pr.get('draft') or pr.get('state')!='open' or pr['base']['ref']!='main': return None
    if pr['head'].get('repo',{}).get('full_name')!=REPO or pr.get('mergeable') is not True: return None
    if pr['user']['login'] in REVIEWERS or pr['user']['login']==app_login: return None
    if 'automation-hold' in {l['name'] for l in pr.get('labels',[])}: return None
    if not {'autofix','heleos-delivery'} <= {l['name'] for l in pr.get('labels',[])}: return None
    if len(files)!=pr['changed_files'] or not files: return None
    if any(p in SENSITIVE_FILES or p.startswith(SENSITIVE) for f in files
           for p in [f['filename'],f.get('previous_filename',f['filename'])]): return None
    sent=[c for c in comments if c.get('user',{}).get('login')==app_login and '<!-- heleos-autofix:' in c.get('body','')]
    if len(sent)>=2 or any(marker in c['body'] for c in sent): return None
    rabbit=[r for r in reviews if r.get('user',{}).get('login')=='coderabbitai[bot]' and r.get('user',{}).get('type')=='Bot']
    if not rabbit: return None
    latest=max(rabbit,key=lambda r:r['id'])
    if latest.get('commit_id')!=head or latest.get('state')!='CHANGES_REQUESTED': return None
    return marker+'\n@coderabbitai autofix stacked pr\n\nCreate a separate candidate only. Preserve checkpoint history, approved rules and tests. Do not merge or change security/automation policy. Independent review and current-head CI are required before integration.'


def protection_ready(rule, settings):
    """Fail closed if the live protection contract drifts or bypasses appear."""
    if rule.get('enforcement')!='active' or rule.get('bypass_actors')!=[]: return False
    if rule.get('conditions',{}).get('ref_name')!={'exclude':[], 'include':['~DEFAULT_BRANCH']}: return False
    if settings.get('default_branch')!='main' or settings.get('allow_merge_commit') is not True: return False
    if settings.get('allow_squash_merge') is not False or settings.get('allow_rebase_merge') is not False: return False
    rules={r['type']:r.get('parameters',{}) for r in rule.get('rules',[])}
    if not {'deletion','non_fast_forward'}<=rules.keys(): return False
    pr=rules.get('pull_request',{})
    if pr.get('required_approving_review_count',0)<1 or pr.get('allowed_merge_methods')!=['merge']: return False
    if not all(pr.get(k) is True for k in ['dismiss_stale_reviews_on_push','require_last_push_approval','required_review_thread_resolution']): return False
    status=rules.get('required_status_checks',{})
    if status.get('strict_required_status_checks_policy') is not True: return False
    required={(c['context'],c.get('integration_id')) for c in status.get('required_status_checks',[])}
    if not {('checks',15368),('dependency-review',15368),('ai-reviewers',15368)}<=required: return False
    return any(t.get('tool')=='CodeQL' and t.get('security_alerts_threshold') in {'high_or_higher','medium_or_higher','all'}
               and t.get('alerts_threshold') in {'errors','errors_and_warnings','all'}
               for t in rules.get('code_scanning',{}).get('code_scanning_tools',[]))


def main_ci_ready(runs, head):
    """Do not deliver on top of failed, pending, or absent mainline CI."""
    relevant=[r for r in runs if r.get('head_sha')==head and r.get('head_branch')=='main'
              and r.get('event')=='push' and r.get('path')=='.github/workflows/ci.yml']
    security=[r for r in runs if r.get('head_sha')==head and r.get('head_branch')=='main'
              and r.get('event') in {'push','dynamic','schedule'}
              and r.get('path')=='dynamic/github-code-scanning/codeql']
    return (bool(relevant) and max(relevant,key=lambda r:r['id']).get('conclusion')=='success'
            and bool(security) and max(security,key=lambda r:r['id']).get('conclusion')=='success')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    if os.environ.get('GITHUB_REPOSITORY')!=REPO: raise SystemExit('Repository mismatch')
    activation=os.environ.get('HELEOS_AUTOMATION_ACTIVE_AFTER','')
    app_login=os.environ.get('HELEOS_AUTOMATION_BOT','')
    if not activation or not app_login.endswith('[bot]'): raise SystemExit('Activation time and automation bot identity required')
    stamp(activation)
    settings=gh('repos/'+REPO)
    if settings.get('private') is not False: raise SystemExit('Only approved public repository scope supported')
    protection=gh(f'repos/{REPO}/rulesets/22285340')
    security_protected=protection_ready(protection,settings)
    if not security_protected: raise SystemExit('Protection contract incomplete or changed; no delivery action taken')
    reports=[]
    for item in pages('repos/'+REPO+'/pulls?state=open'):
        if stamp(item['created_at']) < stamp(activation): continue
        number=item['number'];url=f'repos/{REPO}/pulls/{number}';pr=gh(url)
        files=pages(url+'/files');reviews=pages(url+'/reviews');comments=pages(f'repos/{REPO}/issues/{number}/comments')
        checks=pages(f"repos/{REPO}/commits/{pr['head']['sha']}/check-runs",'check_runs');commits=pages(url+'/commits')
        writers={c.get(role,{}).get('login','') for c in commits for role in ['author','committer'] if isinstance(c.get(role),dict)}
        # Unattributed commit identities do not qualify for unattended delivery.
        attributable=bool(commits) and len(commits)==pr['commits'] and all(c.get('author') and c.get('committer') for c in commits)
        base=gh(f'repos/{REPO}/git/ref/heads/main')['object']['sha']
        result=evaluate(pr,files,reviews,comments,checks,writers,base,activation,app_login,security_protected=security_protected)
        if not attributable: result['eligible']=False;result['reasons'].append('unattributed commit writer')
        result['pr']=number;reports.append(result)
        if not args.apply: continue
        fresh,latest,reviews,comments=refresh_candidate(url,number,pr['head']['sha'],
            files,writers,base,activation,app_login,security_protected=security_protected)
        if fresh['head']['sha']!=pr['head']['sha'] or fresh['base']['sha']!=base: continue
        result.update(latest)
        if not attributable:
            result['eligible']=False;result['reasons'].append('unattributed commit writer')
        message=fix_request(fresh,files,reviews,comments,activation,app_login)
        if message:
            # Log public provider submission before requesting a separate candidate.
            print(json.dumps({'provider':'CodeRabbit','purpose':'bounded stacked candidate fixes','classification':'PUBLIC','source':f'{REPO}#{number}@{pr["head"]["sha"]}','authorization':'owner automation setup; maintainer autofix label','time':dt.datetime.now(dt.timezone.utc).isoformat(),'state':'before_submission'}))
            sent=gh(f'repos/{REPO}/issues/{number}/comments',{'body':message},'POST');result['fix_request']=sent['html_url']
        if result['eligible']:
            runs=pages(f'repos/{REPO}/actions/runs?branch=main&head_sha={base}','workflow_runs')
            if not main_ci_ready(runs,base):
                result['eligible']=False;result['reasons'].append('main CI pending/failed');continue
            security_protected=protection_ready(gh(f'repos/{REPO}/rulesets/22285340'),gh('repos/'+REPO))
            if not security_protected:
                raise SystemExit('Protection changed before merge; no merge attempted')
            fresh,latest,_,_=refresh_candidate(url,number,pr['head']['sha'],
                files,writers,base,activation,app_login,security_protected=security_protected)
            result.update(latest)
            if not latest['eligible']: continue
            # Merge the verified immutable head only. A queued native auto-merge
            # could otherwise outlive this head's sensitive-change consent.
            # The installation has no ruleset bypass; native rules still apply.
            merged=gh(url+'/merge',{'sha':result['head'],'merge_method':'merge'},'PUT')
            if not merged.get('merged'): raise RuntimeError('GitHub refused guarded merge')
            result['merged_commit']=merged['sha']
            result['main_ci']='pending; verify checks and security on resulting main commit'
            break  # Next run must observe this resulting main commit passing CI.
    print(json.dumps(reports,indent=2))


if __name__=='__main__':main()
