from django.contrib.contenttypes.models import ContentType
from .models import UserActionHistory


def _ensure_session_key(request):
    if request is None:
        return ''
    session = getattr(request, 'session', None)
    if session is None:
        return ''
    if not session.session_key:
        session.save()
    return session.session_key or ''


def log_user_action(user, action_type, tool_type, project, project_name, description='',
                    status='success', error_message='', metadata=None,
                    session_key='', request=None):
    if metadata is None:
        metadata = {}

    is_anon_user = not user or not user.is_authenticated
    if is_anon_user:
        user = None
        if not session_key and request is not None:
            session_key = _ensure_session_key(request)
        if not session_key:
            return None
    else:
        session_key = ''

    content_type = ContentType.objects.get_for_model(project)

    history = UserActionHistory.objects.create(
        user=user,
        session_key=session_key,
        action_type=action_type,
        tool_type=tool_type,
        content_type=content_type,
        object_id=project.id,
        project_name=project_name,
        description=description,
        status=status,
        error_message=error_message,
        metadata=metadata
    )

    return history


def finalize_user_action(user, tool_type, project, action_type, status='success',
                         description='', error_message=''):
    content_type = ContentType.objects.get_for_model(project)
    update_fields = {
        'action_type': action_type,
        'status': status,
    }
    if description:
        update_fields['description'] = description
    if error_message:
        update_fields['error_message'] = error_message

    updated = UserActionHistory.objects.filter(
        action_type='project_created',
        content_type=content_type,
        object_id=project.id,
    ).update(**update_fields)

    if updated:
        return None

    if not user or not user.is_authenticated:
        return None

    return log_user_action(
        user=user,
        action_type=action_type,
        tool_type=tool_type,
        project=project,
        project_name=getattr(project, 'name', '') or '',
        description=description,
        status=status,
        error_message=error_message,
    )
