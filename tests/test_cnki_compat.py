from cnkibug import (
    cnki_debug,
    cnki_guard,
    cnki_pagination,
    cnki_records,
    cnki_results,
    cnki_verify,
)


def test_cnki_records_compat_exports_results_parser():
    assert cnki_records.PageParseResult is cnki_results.PageParseResult
    assert cnki_records.parse_result_rows is cnki_results.parse_result_rows


def test_cnki_pagination_compat_exports_results_helpers():
    assert cnki_pagination.get_first_result_href is cnki_results.get_first_result_href
    assert cnki_pagination.get_next_page_marker is cnki_results.get_next_page_marker
    assert cnki_pagination.wait_result_page_advanced is cnki_results.wait_result_page_advanced


def test_cnki_verify_and_debug_compat_exports_guard_helpers():
    assert cnki_verify.handle_verify is cnki_guard.handle_verify
    assert cnki_verify.VERIFY_TIMEOUT == cnki_guard.VERIFY_TIMEOUT
    assert cnki_debug.print_page_debug is cnki_guard.print_page_debug
