from __future__ import annotations

import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from apps.processing.contracts import SourceFile
from apps.processing.exceptions import ProcessingError
from apps.processing.importers.tagged import TaggedCorpusImporter
from apps.processing.importers.xml_like import XmlLikeImporter


class TaggedCorpusImporterTests(SimpleTestCase):
    def source(self, root: Path, name: str, text: str, language: str) -> SourceFile:
        path = root / name
        path.write_text(text, encoding="utf-8")
        return SourceFile(name, name, path, f"tagged_{language}", language)

    def test_imports_chinese_markup_and_warns_about_untagged_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self.source(
                Path(temp_dir),
                "zh.txt",
                "<head>标题</head>\n\n<p><s>农民/n 协会/n 未标注</s></p>",
                "zh",
            )
            result = next(TaggedCorpusImporter().iter_import([source]))

        self.assertEqual([token.text for token in result.tokens], ["农民", "协会"])
        self.assertEqual(result.sentences[0].text, "农民协会")
        self.assertEqual(result.tokens[1].start, 2)
        self.assertIn("skipped 1", result.warnings[0])

    def test_imports_english_tokens_with_spaces_and_multiple_sentences(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self.source(
                Path(temp_dir),
                "en.txt",
                "Farmers_NNS organize_VV0\nWorkers_NNS unite_VV0",
                "en",
            )
            result = next(TaggedCorpusImporter().iter_import([source]))

        self.assertEqual(len(result.sentences), 2)
        self.assertEqual(result.sentences[0].text, "Farmers organize")
        self.assertEqual(result.tokens[1].start, 8)
        self.assertEqual(result.tokens[0].normalized, "farmers")

    def test_rejects_unknown_language_and_files_without_tagged_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            unknown = self.source(root, "unknown.txt", "word_NN", "unknown")
            plain = self.source(root, "plain.txt", "plain text only", "en")
            with self.assertRaisesMessage(ProcessingError, "requires zh/en"):
                next(TaggedCorpusImporter().iter_import([unknown]))
            with self.assertRaisesMessage(ProcessingError, "no valid token"):
                next(TaggedCorpusImporter().iter_import([plain]))


class XmlLikeImporterTests(SimpleTestCase):
    def source(self, root: Path, text: str, language: str = "en") -> SourceFile:
        path = root / "sample.xml"
        path.write_text(text, encoding="utf-8")
        return SourceFile("xml", path.name, path, "xml_like", language)

    def test_imports_explicit_paragraphs_sentences_and_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self.source(
                Path(temp_dir),
                '<?xml version="1.0"?><doc><head>Research Corpus</head>'
                "<p><s>Farmers organize.</s><s>Workers unite.</s></p></doc>",
            )
            result = next(XmlLikeImporter().iter_import([source]))

        self.assertEqual(result.documents[0].title, "Research Corpus")
        self.assertEqual(len(result.paragraphs), 1)
        self.assertEqual(len(result.sentences), 2)
        self.assertGreater(len(result.tokens), 2)

    def test_falls_back_to_root_sentences_and_default_language(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self.source(Path(temp_dir), "第一句。第二句！", language="unknown")
            result = next(XmlLikeImporter().iter_import([source]))

        self.assertEqual(result.documents[0].language, "zh")
        self.assertEqual(result.documents[0].title, "sample.xml")
        self.assertEqual(len(result.sentences), 2)

    def test_rejects_invalid_markup_and_empty_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invalid = self.source(root, "<p>broken")
            with self.assertRaisesMessage(ProcessingError, "Invalid XML-like"):
                next(XmlLikeImporter().iter_import([invalid]))
            empty = self.source(root, "<doc><p>   </p></doc>")
            with self.assertRaisesMessage(ProcessingError, "contains no text"):
                next(XmlLikeImporter().iter_import([empty]))
