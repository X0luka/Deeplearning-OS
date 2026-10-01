"""Offline container backup/restore, including private credentials; never stops services."""
import argparse
import os
from pathlib import Path
import subprocess

WORKER = r'''
import os,sys,tarfile,tempfile
from pathlib import Path, PurePosixPath
from aos.instance_guard import instance_lock
root=Path('/opt/dlos')
data=root/'.local/instances'
os.umask(0o077)
with instance_lock(root,'dev-docker'):
    source=data/'dev-docker'
    if sys.argv[1]=='backup':
        if not source.is_dir():raise ValueError('instance_missing')
        def regular(entry):
            if not (entry.isfile() or entry.isdir()):raise ValueError('unsupported_backup_entry')
            return entry
        with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
            archive.add(source,arcname='dev-docker',filter=regular)
    else:
        if source.exists():raise ValueError('restore_requires_empty_instance')
        with tempfile.TemporaryDirectory(prefix='.restore-',dir=data) as temporary:
            stage=Path(temporary)
            archive_path=stage/'input.tar.gz'
            import shutil
            with archive_path.open('wb') as output:shutil.copyfileobj(sys.stdin.buffer,output)
            with tarfile.open(archive_path,'r:gz') as archive:
                entries=archive.getmembers()
                for entry in entries:
                    name=PurePosixPath(entry.name)
                    if (name.is_absolute() or '..' in name.parts or not name.parts
                            or name.parts[0]!='dev-docker' or not (entry.isfile() or entry.isdir())):
                        raise ValueError('unsafe_backup_entry')
                if not entries:raise ValueError('empty_backup')
                archive.extractall(stage,filter='data')
            restored=stage/'dev-docker'
            if not (restored/'workbench').is_dir():raise ValueError('invalid_instance_backup')
            restored.replace(source)
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('backup','restore'))
    parser.add_argument('file',type=Path)
    parser.add_argument('--project',default='deeplearning-os')
    args=parser.parse_args()
    import re
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*',args.project):parser.error('Invalid Compose project name')
    command=['docker','compose','--project-name',args.project,'run','--rm','--no-deps','-T',
             '--entrypoint','components/AgentOS-K/.venv/bin/python','app','-c',WORKER,args.action]
    path=args.file.absolute()
    if args.action=='backup':
        if path.exists():parser.error('Choose a new backup file; existing files are never overwritten')
        partial=path.with_name(path.name+'.partial')
        fd=os.open(partial,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'wb') as output:subprocess.run(command,stdout=output,check=True,timeout=1200)
            # Exclusive final creation also avoids overwriting a concurrently created file.
            os.link(partial,path)
            partial.unlink()
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    else:
        with path.open('rb') as source:subprocess.run(command,stdin=source,check=True,timeout=1200)
    print(f'{args.action} completed. Backup contains private API credentials; keep it private.')


if __name__=='__main__':
    main()
