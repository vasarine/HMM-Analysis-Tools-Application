import os
from celery import Celery
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "biologine_aplikacija.settings")

app = Celery("biologine_aplikacija")

app.config_from_object("django.conf:settings", namespace="CELERY")

app.autodiscover_tasks()

from biologine_aplikacija.tasks import cleanup_old_projects_task
