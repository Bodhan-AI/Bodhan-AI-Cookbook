"""Errors the app can show to the user in plain language."""


class NarratorError(Exception):
    """Base class for every error this app raises on purpose."""


class InputError(NarratorError):
    """The user's text or choices are not usable (empty, too long, wrong language)."""


class MissingKeyError(NarratorError):
    """No Bodhan API key was found."""


class TruncatedTranslationError(NarratorError):
    """Bodhan cut a translation off, so it must not be read aloud."""

    def __init__(self, paragraph_number):
        super().__init__(
            f"The translation of paragraph {paragraph_number} was cut off by Bodhan, "
            "so no audio was made. Try a shorter paragraph."
        )
        self.paragraph_number = paragraph_number


class BodhanAPIError(NarratorError):
    """Bodhan answered with an error, or could not be reached."""

    def __init__(self, message, status=None, code=None, request_id=None, detail=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id
        self.detail = detail