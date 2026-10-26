from pathlib import Path
import re,subprocess
root=Path(r'G:\codex_workspace\theme-previews')
adb=r'G:\codex_workspace\android-adb\platform-tools\adb.exe'
commands=[];folders=[];count=0
for stage in (root/'inspection/transfer').iterdir():
 if not stage.is_dir() or not re.fullmatch(r'[0-9]+',stage.name):continue
 remote='/sdcard/Download/theme-preview-capture/'+stage.name
 files=[p for p in stage.glob('*.png') if re.fullmatch(r'[0-9]{2}_(initial|left|right)\.png',p.name) and p.stat().st_size>0]
 if files:
  commands.append('rm -f '+' '.join(remote+'/'+p.name for p in files));folders.append(remote);count+=len(files)
for i in range(0,len(commands),15):
 cmd='; '.join(commands[i:i+15])
 subprocess.run([adb,'-s','8ea99c1e','shell',cmd],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
# rmdir removes only empty task-created directories, leaving any unexpected files intact.
for i in range(0,len(folders),30):
 subprocess.run([adb,'-s','8ea99c1e','shell','rmdir '+' '.join(folders[i:i+30])+' 2>/dev/null; true'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
subprocess.run([adb,'-s','8ea99c1e','shell','rmdir /sdcard/Download/theme-preview-capture 2>/dev/null; true'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
print('Removed phone-side temporary screenshot copies after verified PC transfer:',count)