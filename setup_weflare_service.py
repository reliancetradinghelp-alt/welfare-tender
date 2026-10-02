#!/usr/bin/env python3
"""Explicit --apply switches the existing bot to a single systemd service.
No action on unrelated processes, no SIGKILL, no package installation.
"""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time

UNIT='weflare-bot.service'

def running(target):
    result=[]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name)==os.getpid():continue
        try:
            argv=[x.decode() for x in (entry/'cmdline').read_bytes().split(b'\0') if x]
            if not argv or 'python' not in Path(argv[0]).name.lower():continue
            cwd=(entry/'cwd').resolve()
            for arg in argv[1:]:
                if arg.startswith('-'):continue
                possible=Path(arg) if arg.startswith('/') else cwd/arg
                if possible.resolve()==target:
                    cgroup=(entry/'cgroup').read_text()
                    result.append((int(entry.name),cgroup));break
        except (OSError,UnicodeError):continue
    return result

def config(target,python):
    # systemd quoting has different expansion rules; reject ambiguous paths.
    for p in (str(target),str(python)):
        if not all(c.isalnum() or c in '/._-' for c in p):raise ValueError('Нужен путь без пробелов и специальных символов.')
    return f'''# Managed by WeFlare v3 service setup
[Unit]
Description=WeFlare Telegram bot
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
User=root
WorkingDirectory={target.parent}
ExecStart={python} -u {target}
Restart=on-failure
RestartSec=8
TimeoutStopSec=30
UMask=0077
StandardOutput=journal
StandardError=journal
SyslogIdentifier=weflare-bot

[Install]
WantedBy=multi-user.target
'''

def call(args,**kwargs):
    return subprocess.run(args,check=True,**kwargs)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('bot_file',nargs='?',default='/root/bot.py');ap.add_argument('--python',default=sys.executable);ap.add_argument('--apply',action='store_true')
    a=ap.parse_args();target=Path(a.bot_file).resolve();python=Path(a.python).absolute()
    try:
        if os.geteuid()!=0:raise ValueError('Запустите от root на вашем VPS.')
        if not Path('/run/systemd/system').is_dir() or not shutil.which('systemctl'):raise ValueError('На этом сервере не обнаружен работающий systemd.')
        if not target.is_file() or '# WEFLARE_V3_RUNTIME' not in target.read_text():raise ValueError('Сначала установите v3 в этот bot.py.')
        call([str(python),'-c','import telebot, PIL, barcode, time; from weflare_tender import TenderFlow; import weflare_visuals; weflare_visuals.generate_receipt(48126,5,time.time(),time.time())'],cwd=target.parent,timeout=20)
        content=config(target,python);path=Path('/etc/systemd/system')/UNIT
        if path.exists() and path.read_text()!=content and not path.read_text().startswith('# Managed by WeFlare v3'):
            raise ValueError('Служба с таким именем уже существует и создана не этим установщиком. Она не изменена.')
        processes=running(target)
        for pid,cgroup in processes:
            services=[s for s in cgroup.split('/') if s.strip().endswith('.service')]
            if any(s.strip()!=UNIT for s in services):raise ValueError('Бот PID '+str(pid)+' управляется другой службой. Сначала нужно разобрать её запуск.')
        print('Файл: '+str(target));print('Python: '+str(python));print('Текущие процессы: '+str([p for p,_ in processes]));print(content)
        if not a.apply:
            print('ПРОВЕРКА ПРОЙДЕНА. Ничего не изменено. Для переключения добавьте --apply.');return 0
        if path.exists():
            call(['systemctl','stop',UNIT])
        for pid,_ in running(target):
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
        deadline=time.monotonic()+15
        while running(target) and time.monotonic()<deadline:time.sleep(.3)
        if running(target):raise ValueError('Старый бот не завершился. Новый не запущен; принудительного SIGKILL не было.')
        temporary=path.with_suffix('.service.tmp');temporary.write_text(content);temporary.chmod(0o644);os.replace(temporary,path)
        call(['systemctl','daemon-reload']);call(['systemctl','enable','--now',UNIT]);time.sleep(2)
        call(['systemctl','is-active',UNIT])
        print('Служба запущена. Проверка ответа Telegram выполняется отдельно: отправьте /start.')
        print('Журнал: journalctl -u weflare-bot -n 60 --no-pager')
        return 0
    except Exception as e:
        print('ОСТАНОВЛЕНО: '+str(e),file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())
