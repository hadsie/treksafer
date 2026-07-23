"""Tests for block packing and segment math (app/messaging/assembler.py)."""
import pytest

from app.messaging.assembler import (
    Block,
    assemble,
    fits_segment,
    segment_cost,
)


def block(*ladder):
    return Block(ladder=list(ladder))


class TestFitsSegment:
    """Test single-SMS fit across encodings."""

    def test_gsm_boundary(self):
        assert fits_segment('a' * 160)
        assert not fits_segment('a' * 161)

    def test_gsm_extension_chars_cost_two(self):
        assert fits_segment('|' * 80)
        assert not fits_segment('|' * 80 + 'a')

    def test_non_gsm_char_flips_to_ucs2_limit(self):
        assert fits_segment('•' + 'a' * 69)
        assert not fits_segment('•' + 'a' * 70)

    def test_newlines_count_like_any_gsm_char(self):
        assert fits_segment('a\n' * 80)
        assert not fits_segment('a\n' * 80 + 'b')


class TestSegmentCost:
    """Test human-readable segment cost reporting."""

    def test_gsm_text_reports_septets_against_gsm_limit(self):
        assert segment_cost('a' * 148) == '148/160 GSM-7'

    def test_extension_chars_count_double(self):
        assert segment_cost('a|b') == '4/160 GSM-7'

    def test_non_gsm_char_reports_utf16_units_against_ucs2_limit(self):
        assert segment_cost('•' + 'a' * 63) == '64/70 UCS-2'

    def test_surrogate_pair_costs_two_units(self):
        assert segment_cost('🔥') == '2/70 UCS-2'


class TestAssemble:
    """Test greedy whole-block packing."""

    def test_small_blocks_share_a_message(self):
        messages = assemble([block('one'), block('two'), block('three')])
        assert messages == ['one\n\ntwo\n\nthree']

    def test_block_that_does_not_fit_starts_next_message(self):
        a, b = 'a' * 100, 'b' * 100
        messages = assemble([block(a), block(b)])
        assert messages == [a, b]

    def test_blocks_are_never_split(self):
        blocks = [block('x' * 90) for _ in range(5)]
        for message in assemble(blocks):
            assert set(message.replace('\n', '')) == {'x'}
            assert fits_segment(message)

    def test_separator_cost_counts_toward_fit(self):
        # 79 + 2 (separator) + 80 = 161: must not share a message.
        messages = assemble([block('a' * 79), block('b' * 80)], separator='||')
        assert len(messages) == 2

    def test_ladder_downsizes_to_first_fit(self):
        b = block('f' * 200, 'm' * 120, 's' * 20)
        assert assemble([b]) == ['m' * 120]

    def test_oversize_block_gets_own_message(self):
        big = block('z' * 300)
        messages = assemble([block('before'), big, block('after')])
        assert messages == ['before', 'z' * 300, 'after']

    def test_empty_rendering_skipped(self):
        messages = assemble([block('one'), block(''), block('two')])
        assert messages == ['one\n\ntwo']

    def test_no_blocks_returns_no_messages(self):
        assert assemble([]) == []

    def test_join_reconstructs_content(self):
        blocks = [block('a' * 90), block('b' * 90), block('c' * 30)]
        assert '\n\n'.join(assemble(blocks)) == '\n\n'.join(
            b.ladder[0] for b in blocks)


class TestOptionalBlocks:
    """An optional block never gets a message to itself."""

    def test_dropped_when_it_cannot_share(self):
        header = Block(['h' * 90], optional=True)
        fire = Block(['f' * 100])
        assert assemble([header, fire]) == ['f' * 100]

    def test_kept_when_it_shares_a_message(self):
        header = Block(['h' * 20], optional=True)
        fire = Block(['f' * 30])
        assert assemble([header, fire]) == ['h' * 20 + '\n\n' + 'f' * 30]

    def test_trailing_optional_alone_is_dropped(self):
        fire = Block(['f' * 100])
        footer = Block(['x' * 90], optional=True)
        assert assemble([fire, footer]) == ['f' * 100]

    def test_only_optional_blocks_yield_no_messages(self):
        assert assemble([Block(['a'], optional=True)]) == []
