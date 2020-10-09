try:
    # Originally for BadRequest from Django 3.2
    from django.contrib.sessions.exceptions import SessionInterrupted
except ImportError:
    from django.core.exceptions import SuspiciousOperation

    class SessionInterrupted(SuspiciousOperation):
        """
        The session was interrupted.
        """

        pass
