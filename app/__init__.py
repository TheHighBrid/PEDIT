"""Pedit: precision-first PDF editing."""


def __getattr__(name):
    # Preserve uvicorn app:app without initializing the web app when a worker
    # imports only app.editor to unpickle a PDF job.
    if name == 'app':
        from .main import app as application
        return application
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
