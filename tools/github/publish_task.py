"""Publish one explicitly scoped, independently accepted local task.

No dirty-tree discovery, model calls, receipt commands, merges or force pushes.
The checkout controller invokes this after acceptance; GitHub cannot see local files.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

REPO = 'bbukolla-eng/Heleos-spark'
ORIGINS = {'https://github.com/'+REPO+'.git', 'git@github.com:'+REPO+'.git'}
RECEIPT = 'docs/operations/build-checkpoint.json'
VALIDATOR = 'scripts/verify-build-checkpoint.py'


class PublishError(Exception):
    pass


def run(repo, *args, input=None):
    p=subprocess.run(['git','--no-replace-objects','--literal-pathspecs','-C',str(repo),*args],input=input,text=True,capture_output=True)
    if p.returncode: raise PublishError(f'Git {args[0]} failed; inspect this checkout before retrying')
    return p.stdout.strip()


def api(endpoint, data=None, method=None):
    args=['gh','api',endpoint]
    if method:args+=['--method',method]
    if data is not None:args+=['--input','-']
    p=subprocess.run(args,input=json.dumps(data) if data is not None else None,text=True,capture_output=True)
    if p.returncode:raise PublishError('GitHub request failed; local checkpoint is preserved')
    return json.loads(p.stdout) if p.stdout.strip() else {}


def safe_path(name):
    p=PurePosixPath(name)
    return (isinstance(name,str) and bool(name) and not p.is_absolute() and str(p)==name
            and not any(part in {'.','..','.git'} for part in p.parts)
            and not any(c in name for c in ':\\\x00\n\r') and not name.startswith('-'))


def worktree_hash(repo, name):
    path=repo/name
    for ancestor in [path,*path.parents]:
        if ancestor==repo:break
        if ancestor.is_symlink():raise PublishError('Symlink path is outside publisher scope')
    if not path.exists():return None
    if not path.is_file():raise PublishError('Only ordinary files or deletions can be published')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_request(repo, request):
    if request.get('schema_version')!=1:raise PublishError('Unsupported request schema')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}',request.get('task_id','')):raise PublishError('Invalid task identity')
    if not re.fullmatch(r'[0-9a-f]{40}',request.get('base_commit','')):raise PublishError('Full base commit required')
    branch=request.get('branch','')
    if not re.fullmatch(r'codex/[a-z0-9][a-z0-9/-]*',branch):raise PublishError('A codex task branch is required')
    run(repo,'check-ref-format','--branch',branch)
    for key in ['title','body']:
        if not isinstance(request.get(key),str) or not request[key].strip():raise PublishError('Title and body required')
    if '\n' in request['title'] or len(request['title'])>200:raise PublishError('Invalid PR title')
    paths=request.get('paths')
    if not isinstance(paths,dict) or not {RECEIPT,'CURRENT_STATUS.md'}<=paths.keys():raise PublishError('Explicit checkpoint manifest required')
    for name,expected in paths.items():
        if not safe_path(name) or (expected is not None and not re.fullmatch(r'[0-9a-f]{64}',expected)):raise PublishError('Invalid manifest')
        if worktree_hash(repo,name)!=expected:raise PublishError('Worktree differs from approved manifest: '+name)
    receipt=json.loads((repo/RECEIPT).read_text())
    if receipt.get('task_id')!=request['task_id'] or receipt.get('base_commit')!=request['base_commit']:raise PublishError('Receipt identity mismatch')
    if receipt.get('outcome')!='complete' or not isinstance(receipt.get('review'),dict) or receipt['review'].get('outcome')!='accepted':raise PublishError('Independent task acceptance is required')
    if not isinstance(receipt.get('notebooklm_research'),dict) or not receipt['notebooklm_research'].get('disposition'):raise PublishError('Research disposition required')
    entries=receipt.get('changed_files',[])
    declared={f['path']:f['sha256'] for f in entries}
    if len(entries)!=len(declared) or declared!={k:v for k,v in paths.items() if k!=RECEIPT}:raise PublishError('Request and receipt scopes differ')
    staged=set(filter(None,run(repo,'diff','--cached','--name-only','-z').split('\0')))
    if staged-paths.keys():raise PublishError('Unrelated staged paths; leave the index unchanged')
    return receipt


def check_baseline(repo, base, target):
    # Run the validator from the pre-task commit, not a worker's replacement.
    source=run(repo,'show',base+':'+VALIDATOR)
    with tempfile.TemporaryDirectory(prefix='heleos-publish-verify-') as scratch:
        script=Path(scratch)/'verify.py';script.write_text(source)
        args=[sys.executable,str(script),'--repo',str(repo)]+(['--staged'] if target is None else ['--commit',target])
        p=subprocess.run(args,capture_output=True,text=True)
        if p.returncode:raise PublishError('Baseline checkpoint validation failed: '+p.stderr.strip())


def save(path, value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(path)


def publish(repo, request, apply=False):
    repo=Path(repo).resolve()
    if Path(run(repo,'rev-parse','--show-toplevel')).resolve()!=repo:raise PublishError('Repository root required')
    gitdir=Path(run(repo,'rev-parse','--absolute-git-dir')).resolve()
    common=Path(run(repo,'rev-parse','--path-format=absolute','--git-common-dir')).resolve()
    if gitdir==common:raise PublishError('Use an isolated worktree; primary checkout is protected')
    if set(run(repo,'remote','get-url','--push','--all','origin').splitlines())-ORIGINS:raise PublishError('Unapproved push remote')
    validate_request(repo,request)
    base=request['base_commit'];branch=request['branch'];head=run(repo,'rev-parse','HEAD')
    current=run(repo,'branch','--show-current')
    if current and current!=branch:raise PublishError('Use a detached worktree or the declared task branch')
    resumed=head!=base
    if resumed:
        if current!=branch or run(repo,'rev-parse','HEAD^')!=base:raise PublishError('Checkout advanced outside this task')
        changed=set(filter(None,run(repo,'diff-tree','--no-commit-id','--name-only','-r','-z',head).split('\0')))
        if changed!=set(request['paths']):raise PublishError('Committed task scope differs from request')
        if run(repo,'diff','HEAD','--',*request['paths']):raise PublishError('Committed task differs from request')
        check_baseline(repo,base,head)
    if not apply:return {'state':'ready','task_id':request['task_id'],'commit':head if resumed else None}
    live_main=api(f'repos/{REPO}/git/ref/heads/main')['object']['sha']
    ancestry=api(f'repos/{REPO}/compare/{base}...{live_main}')
    if ancestry.get('status') not in {'identical','ahead'} or ancestry.get('merge_base_commit',{}).get('sha')!=base:
        raise PublishError('Task base contains unmerged feature ancestry; do not publish to main')
    state_dir=gitdir/'heleos-delivery';state_dir.mkdir(exist_ok=True)
    lock=state_dir/'publisher.lock'
    try:lock.mkdir()
    except FileExistsError:raise PublishError('Publisher already active or interrupted; inspect its lock before retrying')
    journal=state_dir/(request['task_id']+'.json')
    digest=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
    try:
        if journal.exists() and json.loads(journal.read_text()).get('request_sha256')!=digest:raise PublishError('Task request changed after publication started')
        result={'state':'prepared','request_sha256':digest,'task_id':request['task_id'],'branch':branch}
        save(journal,result)
        if not resumed:
            if not current:run(repo,'switch','-c',branch)
            run(repo,'add','--',*request['paths'])
            expected_tree=run(repo,'write-tree');check_baseline(repo,base,None)
            if run(repo,'write-tree')!=expected_tree or run(repo,'rev-parse','HEAD')!=base:raise PublishError('Candidate changed during validation')
            run(repo,'commit','-m',request['title'])
            head=run(repo,'rev-parse','HEAD')
            if run(repo,'rev-parse','HEAD^{tree}')!=expected_tree or run(repo,'rev-parse','HEAD^')!=base:raise PublishError('Commit hooks changed candidate; do not publish')
        result.update(state='committed',commit=head);save(journal,result)
        result['external_submission']={'provider':'GitHub','purpose':'Publish independently accepted task and open review PR','classification':'approved project code for existing public origin','approved_source_identities':{'commit':head,'paths':request['paths']},'policy_decision':'Owner scoped automatic delivery authorization; completed independent checkpoint','time':dt.datetime.now(dt.timezone.utc).isoformat(),'result_reference':'pending push/PR'}
        save(journal,result)
        run(repo,'push','origin',head+':refs/heads/'+branch)
        result['state']='pushed';save(journal,result)
        prs=api(f'repos/{REPO}/pulls?state=all&head=bbukolla-eng:{branch}&base=main')
        if len(prs)>1:raise PublishError('Multiple open PRs for this task branch')
        marker='<!-- heleos-delivery:'+request['task_id']+' -->'
        if prs:
            pr=prs[0]
            if pr.get('state')!='open':raise PublishError('Task PR is already closed; do not create a duplicate')
            if marker not in pr.get('body','') or pr.get('head',{}).get('sha')!=head:raise PublishError('Existing PR is not bound to this task and commit')
        else:
            pr=api(f'repos/{REPO}/pulls',{'title':request['title'],'head':branch,'base':'main','body':marker+'\n'+request['body'],'draft':False},'POST')
        api(f'repos/{REPO}/issues/{pr["number"]}/labels',{'labels':['heleos-delivery','autofix']},'POST')
        result.update(state='published',pr_number=pr['number'],pr_url=pr['html_url'])
        result['external_submission']['result_reference']=pr['html_url'];save(journal,result)
        return result
    finally:lock.rmdir()


def from_checkpoint(repo, branch=None):
    receipt=json.loads((Path(repo)/RECEIPT).read_text())
    task=receipt['task_id']
    current=run(repo,'branch','--show-current')
    branch=branch or (current if current.startswith('codex/') else 'codex/'+task.lower())
    paths={f['path']:f['sha256'] for f in receipt['changed_files']}
    paths[RECEIPT]=hashlib.sha256((Path(repo)/RECEIPT).read_bytes()).hexdigest()
    body=(receipt['summary']+'\n\nTask: `'+task+'`. Completion evidence: `'+RECEIPT+'`.\n'
          'Independent GitHub review and current-head checks remain required before merging.\n\n'
          +'\n'.join('- '+c['command']+': exit '+str(c['exit_code']) for c in receipt['checks']))
    return {'schema_version':1,'task_id':task,'base_commit':receipt['base_commit'],'branch':branch,
            'title':'chore: deliver '+task,'body':body,'paths':paths}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--request',type=Path);source.add_argument('--from-checkpoint',action='store_true')
    parser.add_argument('--branch');parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:
        request=from_checkpoint(args.repo,args.branch) if args.from_checkpoint else json.loads(args.request.read_text())
        print(json.dumps(publish(args.repo,request,args.apply),indent=2))
    except (PublishError,ValueError,OSError) as exc:raise SystemExit(str(exc))


if __name__=='__main__':main()
