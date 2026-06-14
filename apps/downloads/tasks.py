import os
import shutil
import logging
from celery import shared_task
from django.utils import timezone

log = logging.getLogger('daredevil.downloads.tasks')


@shared_task(name='execute_file_move')
def execute_file_move(file_move_id):
    from .models import FileMove

    try:
        move = FileMove.objects.get(pk=file_move_id)
    except FileMove.DoesNotExist:
        return

    move.status = FileMove.Status.MOVING
    move.save(update_fields=['status'])
    log.info('execute_file_move id=%d: %r → %r', file_move_id, move.source_path, move.dest_path)

    try:
        os.makedirs(move.dest_path, exist_ok=True)
        shutil.move(move.source_path, move.dest_path)
        move.status = FileMove.Status.COMPLETED
        move.completed_at = timezone.now()
        move.error_message = ''
        move.save(update_fields=['status', 'completed_at', 'error_message'])
        log.info('execute_file_move id=%d: done', file_move_id)
    except Exception as e:
        move.status = FileMove.Status.FAILED
        move.error_message = str(e)
        move.save(update_fields=['status', 'error_message'])
        log.error('execute_file_move id=%d: failed — %s', file_move_id, e)
