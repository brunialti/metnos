from env_file import iter_env, read_env, read_first


def test_env_parser_is_data_only_and_tolerant(tmp_path):
    path = tmp_path / "sample.env"
    path.write_text(
        "# comment\n A = 'one'\nB=\"two\"\nmalformed\n=empty-key\n",
        encoding="utf-8",
    )
    assert list(iter_env(path)) == [("A", "one"), ("B", "two")]
    assert read_env(path) == {"A": "one", "B": "two"}


def test_read_first_respects_file_and_line_order(tmp_path):
    first = tmp_path / "first.env"
    second = tmp_path / "second.env"
    first.write_text("KEY=first\nKEY=later\n", encoding="utf-8")
    second.write_text("KEY=second\n", encoding="utf-8")
    assert read_first("KEY", [tmp_path / "missing", first, second]) == "first"
