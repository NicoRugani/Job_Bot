import pytest

from jobbot.config import PrefilterConfig
from jobbot.prefilter import KeywordScorer, Prescorer, TitleClassifier, chunk_text


@pytest.mark.parametrize(
    ("title", "group"),
    [
        ("Software Engineer Intern", "swe"),
        ("Software Engineer Intern - Backend", "swe"),
        ("Front-End Developer Intern", "swe"),
        ("Quantitative Developer Intern", "swe"),
        ("Software Engineer Intern - Trading Systems", "swe"),
        ("UX Design Intern", "ux"),
        ("Product Designer Intern", "ux"),
        ("UI/UX Engineering Intern", "ux"),
        ("Long Range Platform Embedded Firmware Intern - Summer 2027", "embedded"),
        ("Hardware Engineering Intern", "embedded"),
        ("Data Engineering Intern/Co-op", "generic"),
        ("Intern - Multiple Teams", "generic"),
        ("Quantitative Researcher – Intern", None),
        ("Software Engineer Intern/Co-op - Masters", None),
        ("Research Scientist PhD Intern", None),
        ("Product Manager Intern", None),
        ("Mechanical Engineering Intern", None),
        ("Sales Engineer Intern", None),
        ("Business Analyst Intern", None),
        ("Senior Software Engineer", None),
    ],
)
def test_title_classification(config, title, group):
    assert TitleClassifier(config.roles).classify(title).group == group


def test_filtered_titles_explain_why(config):
    verdict = TitleClassifier(config.roles).classify("Marketing Intern")
    assert not verdict.keep and verdict.reason


def test_keyword_scorer_respects_word_boundaries():
    scorer = KeywordScorer(["Java", "JavaScript", "C#", "React", "Node.js"], saturation=4)
    score, hits = scorer.score("We use JavaScript, React Native and some C#. Node.js is a plus.")
    assert sorted(hits) == ["c#", "javascript", "node.js", "react"]
    assert score == 100
    assert scorer.score("Strong Java background")[1] == ["java"]


def test_chunk_text_covers_whole_text():
    text = "x" * 3000
    chunks = chunk_text(text, size=1200, overlap=200)
    assert chunks[0] == text[:1200] and chunks[-1].endswith("x") and sum(map(len, chunks)) >= 3000
    assert chunk_text("short") == ["short"]


def test_prescorer_keyword_only_mode(profile):
    from jobbot.profile import profile_embedding_text, profile_keywords

    cfg = PrefilterConfig(use_embeddings=False, keyword_saturation=4)
    prescorer = Prescorer(cfg, profile_keywords(profile), profile_embedding_text(profile))
    relevant = prescorer.score("Frontend Intern", "Build React components in JavaScript and TypeScript with Figma designs.", 1.0)
    irrelevant = prescorer.score("Mechanical Intern", "SolidWorks CAD and tolerance analysis.", 1.0)
    assert relevant == 100 and irrelevant == 0
    assert prescorer.score("Frontend Intern", "React JavaScript TypeScript Figma", 0.5) == 50
