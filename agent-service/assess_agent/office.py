"""Refresh generated Word fields using an isolated, hidden local Word instance."""
import json
from pathlib import Path
import shutil
import subprocess
import sys


def stop_owned_word(owner: Path):
    """Only stop the new Word process identified by this helper, never all Word apps."""
    try:
        metadata = json.loads(owner.read_text(encoding='utf-8-sig'))
        if metadata.get('owned') is not True:
            return
        pid, ticks = int(metadata['pid']), int(metadata['startedTicks'])
        if pid <= 0 or ticks <= 0:
            return
        executable = shutil.which('pwsh.exe') or shutil.which('powershell.exe')
        script = (f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
                  f"if ($p -and $p.ProcessName -eq 'WINWORD' -and "
                  f"$p.StartTime.ToUniversalTime().Ticks -eq {ticks}) "
                  "{ Stop-Process -Id $p.Id -Force; $p.WaitForExit(5000) | Out-Null }")
        subprocess.run([executable, '-NoProfile', '-NonInteractive', '-Command', script],
                       capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        pass


def remove_working_copy(directory: Path, parent: Path):
    # A leftover Office lock must never turn a successful report into a failure.
    target = directory.resolve()
    if target.parent == parent.resolve() and target.name.startswith('word-fields-'):
        shutil.rmtree(target, ignore_errors=True)


def refresh_word_fields(path: Path) -> dict:
    if sys.platform != 'win32':
        return {'status':'manualUpdateRequired', 'reason':'nativeWordUnavailable'}
    executable = shutil.which('pwsh.exe') or shutil.which('powershell.exe')
    if not executable:
        return {'status':'manualUpdateRequired', 'reason':'nativeWordUnavailable'}
    # Word never opens the export file itself. A timeout can only lock this
    # disposable working copy; the original stays available for final delivery.
    from tempfile import mkdtemp
    directory = Path(mkdtemp(prefix='word-fields-', dir=path.parent))
    source, target, owner = directory / 'source.docx', directory / 'updated.docx', directory / 'owner.json'
    try:
        shutil.copyfile(path, source)
        result = subprocess.run([executable,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
            str(Path(__file__).resolve().parents[1]/'scripts/update-word-fields.ps1'),
            '-Source',str(source.resolve()),'-Output',str(target.resolve()), '-OwnerRecord', str(owner.resolve())],
            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode != 0 or not target.is_file():
            return {'status':'manualUpdateRequired', 'reason':'fieldUpdateFailed'}
        metadata = json.loads(result.stdout.strip())
        if not isinstance(metadata, dict) or not isinstance(metadata.get('pages'), int):
            return {'status':'manualUpdateRequired', 'reason':'fieldUpdateFailed'}
        target.replace(path)
        return {'status':'updated', 'pages':metadata['pages']}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'status':'manualUpdateRequired', 'reason':'fieldUpdateFailed'}
    finally:
        stop_owned_word(owner)
        remove_working_copy(directory, path.parent)
