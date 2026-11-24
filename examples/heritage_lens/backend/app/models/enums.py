import enum


class Privacy(str, enum.Enum):
    private = "private"
    public = "public"


class PublicationStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class StoryCategory(str, enum.Enum):
    oral_history = "oral_history"
    festival = "festival"
    traditional_art = "traditional_art"
    historical_document = "historical_document"
    architecture = "architecture"
    traditional_music = "traditional_music"
    dance = "dance"
    food_recipes = "food_recipes"
    indigenous_knowledge = "indigenous_knowledge"
    personal_memory = "personal_memory"


class AudioKind(str, enum.Enum):
    original = "original"    # a human recording; never modified by processing
    generated = "generated"  # narration produced by indic-speak


class OcrStatus(str, enum.Enum):
    not_run = "not_run"
    completed = "completed"
    partial = "partial"
    failed = "failed"


class DocumentType(str, enum.Enum):
    letter = "letter"
    manuscript = "manuscript"
    printed = "printed"
    photograph = "photograph"
    other = "other"


class CapsuleStatus(str, enum.Enum):
    draft = "draft"
    sealed = "sealed"  # saved with a release date; NOT an automatic-delivery guarantee
