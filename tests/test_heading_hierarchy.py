import unittest
from postprocess import clean_markdown_heading_hierarchy

class TestHeadingHierarchyCascade(unittest.TestCase):
    def test_consecutive_same_level(self):
        """## H1 followed immediately by ## H2 without text should become ## H1 and ### H2."""
        md = (
            "## Heading 1\n"
            "## Heading 2\n"
            "\n"
            "Body text under heading 2.\n"
        )
        cleaned, count = clean_markdown_heading_hierarchy(md)
        expected = (
            "## Heading 1\n"
            "### Heading 2\n"
            "\n"
            "Body text under heading 2.\n"
        )
        self.assertEqual(cleaned, expected)
        self.assertGreater(count, 0)

    def test_inverted_headings_in_cluster(self):
        """### Parent followed immediately by ## Child -> inverts to ## Parent, ### Child."""
        md = (
            "### Section Title\n"
            "## Subsection Title\n"
            "Content under subsection.\n"
        )
        cleaned, count = clean_markdown_heading_hierarchy(md)
        expected = (
            "## Section Title\n"
            "### Subsection Title\n"
            "Content under subsection.\n"
        )
        self.assertEqual(cleaned, expected)
        self.assertGreater(count, 0)

    def test_cascade_to_child_headings_across_body_text(self):
        """
        When ## H2 is shifted to ### H2, any sub-subheadings under H2
        (separated by body text) MUST also cascade (+1) so they remain children.
        And the cascade must stop at the next same-level parent (## Sibling).
        """
        md = (
            "## Parent 1\n"
            "## Parent 2\n"
            "\n"
            "Body text 1.\n"
            "\n"
            "### Child of Parent 2\n"
            "\n"
            "Body text 2.\n"
            "\n"
            "#### Grandchild of Parent 2\n"
            "\n"
            "Body text 3.\n"
            "\n"
            "## Sibling of Parent 1\n"
            "\n"
            "Body text 4.\n"
        )
        cleaned, count = clean_markdown_heading_hierarchy(md)
        expected = (
            "## Parent 1\n"
            "### Parent 2\n"
            "\n"
            "Body text 1.\n"
            "\n"
            "#### Child of Parent 2\n"
            "\n"
            "Body text 2.\n"
            "\n"
            "##### Grandchild of Parent 2\n"
            "\n"
            "Body text 3.\n"
            "\n"
            "## Sibling of Parent 1\n"
            "\n"
            "Body text 4.\n"
        )
        self.assertEqual(cleaned, expected)

    def test_three_consecutive_headings(self):
        """Three consecutive headings at same level #, #, # should cascade to #, ##, ###."""
        md = (
            "# Top\n"
            "# Middle\n"
            "# Bottom\n"
            "\n"
            "Body text.\n"
        )
        cleaned, count = clean_markdown_heading_hierarchy(md)
        expected = (
            "# Top\n"
            "## Middle\n"
            "### Bottom\n"
            "\n"
            "Body text.\n"
        )
        self.assertEqual(cleaned, expected)

    def test_code_blocks_unaffected(self):
        """Headings / hashes inside code fences must remain untouched."""
        md = (
            "## Heading 1\n"
            "```python\n"
            "# comment line\n"
            "## not a heading\n"
            "```\n"
            "## Heading 2\n"
            "Text.\n"
        )
        # Note: Heading 1 is separated from Heading 2 by the code block,
        # so they are NOT consecutive without intervening content.
        cleaned, count = clean_markdown_heading_hierarchy(md)
        self.assertEqual(cleaned, md)
        self.assertEqual(count, 0)

    def test_level_overflow_converts_to_bold(self):
        """When cascading causes a heading level to exceed 6, convert it cleanly to **Bold**."""
        md = (
            "###### Level 6\n"
            "###### Sub Level\n"
            "Text\n"
        )
        cleaned, count = clean_markdown_heading_hierarchy(md)
        expected = (
            "###### Level 6\n"
            "**Sub Level**\n"
            "Text\n"
        )
        self.assertEqual(cleaned, expected)

    def test_already_perfect_hierarchy_untouched(self):
        """Headings already in strict parent-child order should not be altered."""
        md = (
            "# Chapter 1\n"
            "## Section 1.1\n"
            "### Subsection 1.1.1\n"
            "\n"
            "Some text.\n"
        )
        cleaned, count = clean_markdown_heading_hierarchy(md)
        self.assertEqual(cleaned, md)
        self.assertEqual(count, 0)

if __name__ == '__main__':
    unittest.main()

