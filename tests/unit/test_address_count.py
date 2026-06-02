"""Unit tests for min_prefix_v4, min_prefix_v6, address_count_from_prefix."""
import pytest
from ipam import min_prefix_v4, min_prefix_v6, address_count_from_prefix


@pytest.mark.unit
class TestMinPrefixV4:
    def test_single_host(self):
        assert min_prefix_v4(1) == 32

    def test_p2p(self):
        assert min_prefix_v4(2) == 31  # RFC 3021

    def test_three_to_six(self):
        # /29 = 6 usable; need 3-6 hosts
        for n in range(3, 7):
            assert min_prefix_v4(n) == 29, f'n={n}'

    def test_seven_to_fourteen(self):
        for n in range(7, 15):
            assert min_prefix_v4(n) == 28, f'n={n}'

    def test_thirty(self):
        # 30 usable hosts needs /27 (32-5=27)
        assert min_prefix_v4(30) == 27

    def test_thirty_one(self):
        assert min_prefix_v4(31) == 26  # /26 has 62 usable

    def test_sixty_two(self):
        assert min_prefix_v4(62) == 26

    def test_sixty_three(self):
        # /25 = 126 usable
        assert min_prefix_v4(63) == 25

    def test_no_slash_30_anomaly(self):
        # /30 should never be the answer for 3+ hosts — /29 beats it
        for n in range(3, 7):
            prefix = min_prefix_v4(n)
            assert prefix != 30, f'Got /30 for n={n} — should be /29'


@pytest.mark.unit
class TestMinPrefixV6:
    def test_single(self):
        assert min_prefix_v6(1) == 128

    def test_two(self):
        assert min_prefix_v6(2) == 127

    def test_three_four(self):
        assert min_prefix_v6(3) == 126
        assert min_prefix_v6(4) == 126

    def test_five_to_eight(self):
        for n in range(5, 9):
            assert min_prefix_v6(n) == 125, f'n={n}'


@pytest.mark.unit
class TestAddressCountFromPrefix:
    def test_v4_host(self):
        assert address_count_from_prefix(32, 4) == 1

    def test_v4_p2p(self):
        assert address_count_from_prefix(31, 4) == 2

    def test_v4_29(self):
        assert address_count_from_prefix(29, 4) == 6

    def test_v4_24(self):
        assert address_count_from_prefix(24, 4) == 254

    def test_v6_128(self):
        assert address_count_from_prefix(128, 6) == 1

    def test_v6_127(self):
        assert address_count_from_prefix(127, 6) == 2

    def test_v6_64(self):
        assert address_count_from_prefix(64, 6) == 2 ** 64

    def test_roundtrip_v4(self):
        """min_prefix_v4(address_count_from_prefix(pl)) <= pl for usable counts.

        /30 is the known exception: it has 2 usable hosts and maps to /31 (RFC 3021
        is more efficient — /31 also gives 2 addresses with no net/bcast overhead).
        """
        for pl in range(20, 33):
            ac = address_count_from_prefix(pl, 4)
            back = min_prefix_v4(ac)
            if pl == 30:
                assert back == 31, f'/30 should map to /31 but got /{back}'
            else:
                assert back <= pl, f'pl={pl} ac={ac} back={back}'
