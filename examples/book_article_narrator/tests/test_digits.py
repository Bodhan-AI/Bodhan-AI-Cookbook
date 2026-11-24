from narrator import config
from narrator.text_utils import to_native_digits

HINDI = "०१२३४५६७८९"


def test_digits_become_hindi_digits():
    text = "शुल्क ₹1,250 है। तिथि 15 नवंबर 2026 है।"
    assert to_native_digits(text, HINDI) == "शुल्क ₹१,२५० है। तिथि १५ नवंबर २०२६ है।"


def test_phone_numbers_are_converted_too():
    assert to_native_digits("1800-123-4567", HINDI) == "१८००-१२३-४५६७"


def test_numbers_stuck_to_english_letters_are_left_alone():
    assert to_native_digits("5G और COVID19 और A4", HINDI) == "5G और COVID19 और A4"


def test_no_digits_given_means_no_change():
    assert to_native_digits("1,250", None) == "1,250"


def test_hindi_is_set_up_in_the_config():
    assert config.NATIVE_DIGITS["hi"] == HINDI