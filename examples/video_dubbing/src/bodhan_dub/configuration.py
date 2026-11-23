"""Safe, bounded configuration for voices and translation glossary hints."""
from pathlib import Path
import yaml

from .media import MediaError
from .synthesis import ALL_VOICES
from .render import PRODUCTION_LANGUAGES


def mapping_file(path):
    if path is None:return {}
    try:
        body=Path(path).read_text(encoding='utf-8')
        if len(body)>65536:raise ValueError
        value=yaml.safe_load(body)
        if not isinstance(value,dict) or not value:raise ValueError
        return value
    except (OSError,ValueError,yaml.YAMLError) as exc:
        raise MediaError('invalid_configuration','Expected a nonempty YAML/JSON mapping of at most 64 KB.') from exc


def voice_configuration(path,langs):
    values=mapping_file(path)
    if any(k not in langs or not isinstance(v,str) or v not in ALL_VOICES for k,v in values.items()):
        raise MediaError('invalid_voice_configuration','Voice configuration must map requested language codes to documented voice names.')
    return values


def glossary_configuration(path):
    values=mapping_file(path)
    if len(values)>100:raise MediaError('invalid_glossary','Use at most 100 source terms.')
    for term,targets in values.items():
        if not isinstance(term,str) or not term.strip() or len(term)>100 or not isinstance(targets,dict) or not targets:
            raise MediaError('invalid_glossary','Map each English source term to language-code/preferred-term mappings.')
        if any(lang not in PRODUCTION_LANGUAGES or not isinstance(text,str) or not text.strip() or len(text)>200 for lang,text in targets.items()):
            raise MediaError('invalid_glossary','Glossary targets must be bounded strings under supported language codes.')
    return values
