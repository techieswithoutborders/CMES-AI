"""Unit tests for the MeSH categorization framework."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mesh"))

from mesh_categorizer import ConceptExtractor, MeshMapper, run_categorization  # noqa: E402
from mesh_hierarchy import MeshHierarchy  # noqa: E402
from mesh_vocab import MeshVocab, default_vocab_dir  # noqa: E402
from mesh_categorizer import ConceptCandidate  # noqa: E402


def load_vocab():
    vocab = MeshVocab()
    vocab.load_bundle(default_vocab_dir())
    return vocab


def test_vocab_loads():
    vocab = load_vocab()
    assert len(vocab.descriptors) > 50
    assert vocab.descriptor_by_id("D000001").preferred_term == "Urinary Tract Infections"
    assert vocab.descriptor_by_id("D000001").tree_number == "C01.252.850"
    assert vocab.category("D000001") == "C"
    assert len(vocab.qualifiers) > 0


def test_entry_term_exact_match():
    vocab = load_vocab()
    descs = vocab.descriptors_by_term("sepsis")
    assert any(d.preferred_term == "Sepsis" for d in descs)
    descs = vocab.descriptors_by_term("uti")
    assert any(d.preferred_term == "Urinary Tract Infections" for d in descs)


def test_most_specific_pick():
    vocab = load_vocab()
    # urinary tract infection (C01.252.850, depth 3) vs cystitis (depth 4)
    uti = vocab.descriptor_by_id("D000001")
    cystitis = vocab.descriptor_by_id("D000002")
    mapper = MeshMapper(vocab)
    picked = mapper._pick_most_specific([uti, cystitis])
    assert picked.descriptor_id == "D000002"  # cystitis is more specific


def test_ancestors_and_descendants():
    vocab = load_vocab()
    hierarchy = MeshHierarchy(vocab)
    anc = hierarchy.ancestors("D000002")  # cystitis C01.252.850.250
    tns = [a.tree_number for a in anc]
    assert "C01.252.850" in tns  # urinary tract infections is an ancestor
    assert "C01.252.850.250" in tns  # cystitis itself (include_self=True)
    desc = hierarchy.descendants("D000001")  # UTI
    desc_ids = {d.descriptor_id for d in desc}
    assert "D000002" in desc_ids  # cystitis is a descendant


def test_category_rollup():
    vocab = load_vocab()
    hierarchy = MeshHierarchy(vocab)
    assert vocab.category_name("C") == "Diseases"
    assert vocab.category_name("D") == "Chemicals and Drugs"
    tops = hierarchy.top_level_descriptors("C")
    assert any(d.preferred_term == "Infection" for d in tops)


def test_qualifier_inference():
    vocab = load_vocab()
    mapper = MeshMapper(vocab)
    cand = ConceptCandidate(
        text="urinary tract infection",
        sentence="Treatment of urinary tract infection with antibiotics.",
        doc_id="doc1", page=1, start_char=0, end_char=25,
    )
    hit = mapper._map_one(cand)
    assert hit is not None
    assert hit.preferred_term == "Urinary Tract Infections"
    # qualifier inferred from "Treatment" -> therapy
    assert hit.qualifier_inferred == "therapy"


def test_run_categorization_smoke():
    vocab = load_vocab()
    content_dir = Path(__file__).resolve().parent.parent / "SampleFiles" / "Content"
    hits, vocab2 = run_categorization(content_dir, vocab_dir=default_vocab_dir(), limit_docs=5)
    assert len(hits) >= 0
    # every hit should have a category and descriptor
    for h in hits:
        assert h.descriptor_id
        assert h.preferred_term
        assert h.confidence > 0
