"""Keep final downloads after success and only recoverable state after failure."""
from hashlib import sha256
import json
import logging
from pathlib import Path
import shutil
from uuid import UUID

logger = logging.getLogger(__name__)


def task_directory(jobs_root: Path, task_id: str) -> Path:
    if str(UUID(task_id)) != task_id:
        raise ValueError('Invalid task directory')
    root = jobs_root.resolve()
    directory = root / task_id
    if directory.resolve() != directory or directory.parent != root:
        raise ValueError('Task directory escapes jobs root')
    # Refuse links/junctions anywhere inside a task before removing any files.
    for path in directory.rglob('*'):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()) or path.resolve() != path.absolute():
            raise ValueError('Linked cache path')
    return directory


def remove_cache(path: Path):
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def cleanup_successful_task(jobs_root, jobs, task_id, attempt):
    row = jobs.get(task_id)
    if row['status'] != 'SUCCEEDED' or row['attempt'] != attempt:
        return False
    directory = task_directory(jobs_root, task_id)
    current = directory / f'attempt-{attempt}'
    artifacts = json.loads(row['result'])['artifacts']
    if 'report' not in artifacts or not artifacts.keys() <= {'report', 'diagnosis'}:
        raise ValueError('Unknown download artifacts')
    keep = {current / f'{kind}.docx' for kind in artifacts}
    # Verify every download before any deletion. The database owns its manifest.
    for kind, artifact in artifacts.items():
        file = current / f'{kind}.docx'
        if not file.is_file() or sha256(file.read_bytes()).hexdigest() != artifact['sha256']:
            raise ValueError('Download integrity check failed')
    for child in directory.iterdir():
        if child == current:
            for file in current.iterdir():
                if file not in keep:
                    remove_cache(file)
        else:
            remove_cache(child)
    return True


def cleanup_unfinished_task(jobs_root, jobs, task_id, attempt):
    row = jobs.get(task_id)
    if row['attempt'] != attempt or row['status'] not in ('FAILED', 'CANCELLED', 'INTERRUPTED'):
        return False
    directory = task_directory(jobs_root, task_id)
    current = directory / f'attempt-{attempt}'
    if not current.is_dir():
        return False
    marker = current / 'recovery-consolidated.json'
    if not marker.is_file():
        # Old failed jobs can rely on stage-5 + audit; leave them recoverable.
        return False
    mode = json.loads(marker.read_text(encoding='utf-8'))['mode']
    if (current / 'export-checkpoint.json').is_file():
        from .export_resume import load_export_snapshot
        from .schemas import JobRequest
        load_export_snapshot(JobRequest.model_validate_json(row['request']), current)
        # Export recovery is a frozen snapshot; writing checkpoints are redundant.
        keep = {'export-checkpoint.json', 'corpus.json', 'corpus.json.gz',
                'visual-assets.json', 'visual-coverage.json', 'data-utilization.json',
                'visuals', 'recovery-consolidated.json'}
    elif mode == 'writing':
        keep = {'checkpoints', 'recovery-consolidated.json'}
    else:
        return False
    for child in current.iterdir():
        if child.name not in keep:
            remove_cache(child)
    # All usable checkpoints or the verified export snapshot are now in current.
    for previous in directory.glob('attempt-*'):
        number = previous.name.removeprefix('attempt-')
        if previous.is_dir() and number.isdigit() and int(number) < attempt:
            remove_cache(previous)
    return True


def cleanup_terminal_task(settings, jobs, task_id, attempt):
    try:
        if not cleanup_successful_task(settings.jobs_root(), jobs, task_id, attempt):
            cleanup_unfinished_task(settings.jobs_root(), jobs, task_id, attempt)
    except Exception as error:
        # A cleanup failure cannot change a successful report into a failed task.
        logger.warning('Task %s cache cleanup deferred: %s', task_id, type(error).__name__)


def cleanup_completed_jobs(settings, jobs):
    # Do not use the UI's 1000-record limit for storage maintenance.
    connection = jobs.connect()
    try:
        rows = connection.execute("SELECT id, attempt FROM jobs WHERE status='SUCCEEDED'").fetchall()
    finally:
        connection.close()
    for row in rows:
        cleanup_terminal_task(settings, jobs, row['id'], row['attempt'])
