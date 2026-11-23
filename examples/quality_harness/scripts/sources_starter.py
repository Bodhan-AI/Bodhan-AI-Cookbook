"""Write data/sources.jsonl: the English source texts of the demo dataset, with attribution.

Wikipedia texts are adapted (lightly edited: pronunciation brackets removed, small grammar fixes) from
Simple English Wikipedia, CC BY-SA 4.0, at the revision given. Items marked "original" were written for
this dataset around general facts and standard formulas.

Run once; sources.jsonl is the stable artifact (Wikipedia pages change, so this is not re-fetched).
"""

import json
from pathlib import Path

WIKI = "https://simple.wikipedia.org/w/index.php?oldid={}"


def wiki(revid):
    return {"source": WIKI.format(revid), "license": "CC BY-SA 4.0", "adapted": True}


ORIGINAL = {"source": "original, written for this dataset", "license": "same as this repository"}

SOURCES = [
    # short
    ("short-tea", "short", "Tea is a caffeinated drink made from steeping the leaves of the Camellia sinensis plant.", wiki(11057736)),
    ("short-rain", "short", "Rain is a kind of precipitation. It is measured by a rain gauge.", wiki(10633726)),
    ("short-library", "short", "A library is a place where many books are kept. Most libraries let people borrow books for several weeks.", wiki(10901378)),
    ("short-monsoon", "short", "A monsoon is a seasonal wind which lasts for several months.", wiki(10756168)),
    ("short-photosynthesis", "short", "Photosynthesis is a process in which green plants make their own food from sunlight.", wiki(10903963)),
    ("short-heart", "short", "The heart is a hard-working, fist-sized muscle found in people and animals. It pumps blood through tube-like structures called blood vessels.", wiki(11049517)),
    ("short-honeybee", "short", "A honey bee is any bee that is a member of the genus Apis. Honey bees live in colonies of various sizes.", wiki(10938569)),
    ("short-volcano", "short", "A volcano is a mountain that has lava coming out from a magma chamber under the ground.", wiki(11074962)),
    # numbers, dates, units
    ("num-everest", "numbers", "Mount Everest is the highest mountain on Earth. It is about 8,848 metres (29,029 feet) high.", wiki(11072944)),
    ("num-railways", "numbers", "Indian Railways has a total route length of 67,956 km as of 31 March 2022. About 83% of its broad-gauge routes are electrified.", wiki(11068774)),
    ("num-light", "numbers", "The speed of light in a vacuum is exactly 299,792,458 metres per second.", wiki(10994097)),
    ("num-elephant", "numbers", "At birth, an elephant calf may weigh as much as 100 kg. The baby elephant develops for 20 to 22 months inside its mother.", wiki(10879973)),
    ("num-un", "numbers", "The United Nations was established on 24 October 1945. When it was founded, it had 51 members. Now there are 193.", wiki(11059479)),
    ("num-chennai", "numbers", "Chennai has a population of about 7 million people. It was founded in 1661 by the British East India Company.", wiki(10847699)),
    ("num-kolkata", "numbers", "Kolkata was spelled Calcutta before 1 January 2001. About 4.5 million people live in the city.", wiki(10836445)),
    ("num-water", "numbers", "At sea level, water boils at 100 °C and freezes at 0 °C.", ORIGINAL),
    # names and terms
    ("ent-kalam", "entities", "A. P. J. Abdul Kalam was a famous Indian aerospace engineer. He was the 11th President of India, from 2002 to 2007.", wiki(11019913)),
    ("ent-tagore", "entities", "Rabindranath Tagore was an Indian Bengali poet, philosopher, and artist. He wrote many stories, novels, poems, and dramas.", wiki(10849741)),
    ("ent-isro", "entities", "The Indian Space Research Organisation, also known as ISRO, was created in 1969. It built and launched the Mars Orbiter Mission.", wiki(10939197)),
    ("ent-curie", "entities", "Marie Curie was a Polish-born French physicist and chemist. She was the first woman to win a Nobel Prize and the first person to win two Nobel Prizes.", wiki(11001748)),
    ("ent-rbi", "entities", "The Reserve Bank of India (RBI) is a top banking institution of the Republic of India. The bank controls the money supply in the country.", wiki(10665899)),
    ("ent-tajmahal", "entities", "The Taj Mahal was built in the 17th century by Mughal Emperor Shah Jahan in memory of his wife, Mumtaz Mahal. The building is in the city of Agra, Uttar Pradesh.", wiki(10982428)),
    ("ent-tiger", "entities", "The Bengal tiger (Panthera tigris) is a tiger subspecies native to the Indian subcontinent. It lives in Bhutan, Nepal, Bangladesh and northern India.", wiki(10998316)),
    # Markdown
    ("md-cities", "markdown",
     "## Two coastal cities\n\n| City | State | Founded |\n|---|---|---|\n| Chennai | Tamil Nadu | 1661 |\n| Kolkata | West Bengal | 1690 |\n\n"
     "Chennai is on the coast of the **Bay of Bengal**, and Kolkata is on the bank of the **Hooghly River**.", ORIGINAL),
    ("md-bicycle", "markdown",
     "### Caring for a bicycle\n\n1. Keep the chain clean and oiled.\n2. Check the air in both tyres every week.\n3. Store the bike in a dry place.\n\n"
     "A well-kept bike can last for many years.", ORIGINAL),
    ("md-data", "markdown",
     "## Reading data files\n\nUse the `read_csv` function to load a table. See the [pandas documentation](https://pandas.pydata.org/docs/) for details.\n\n"
     "- Each row is one record.\n- Each column has a **name**.", ORIGINAL),
    ("md-seasons", "markdown",
     "| Season | Months | Rain |\n|---|---|---|\n| Summer | March to May | Low |\n| Monsoon | June to September | Heavy |\n| Winter | December to February | Low |\n\n"
     "Most of India's yearly rain falls during the *monsoon*.", ORIGINAL),
    # LaTeX
    ("tex-kinetic", "latex", "The kinetic energy of a moving object is $E_k = \\frac{1}{2}mv^2$, where $m$ is its mass and $v$ is its speed.", ORIGINAL),
    ("tex-newton", "latex", "Newton's second law states that $F = ma$. A force of 10 N acting on a mass of 2 kg gives an acceleration of 5 metres per second squared.", ORIGINAL),
    ("tex-circle", "mixed", "## Area of a circle\n\nThe area of a circle with radius $r$ is\n\n$$A = \\pi r^2$$\n\nFor a radius of 3 cm, the area is about 28.27 square centimetres.", ORIGINAL),
]


def main():
    out = Path(__file__).resolve().parent.parent / "data" / "sources.jsonl"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for sid, kind, text, attribution in SOURCES:
            f.write(json.dumps({"id": sid, "kind": kind, "source": text, "attribution": attribution}, ensure_ascii=False) + "\n")
    print(f"wrote {len(SOURCES)} sources to {out}")


if __name__ == "__main__":
    main()
