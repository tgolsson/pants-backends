from pants_backend_cc.util_rules.scan import is_header_like, parse_depfile, scan_argv


def test_is_header_like():
    assert is_header_like("src/a.h")
    assert is_header_like("src/a.hpp")
    assert is_header_like("include/vendored/core")
    assert not is_header_like("src/a.cpp")
    assert not is_header_like("notes.txt")


def test_scan_argv():
    command = "/usr/bin/c++ -DX -I../src -O3 -MD -MT obj/a.o -MF obj/a.o.d -o obj/a.o -c ../src/a.cpp"
    assert scan_argv(command.split()) == [
        "/usr/bin/c++",
        "-DX",
        "-I../src",
        "-O3",
        "../src/a.cpp",
        "-M",
        "-MG",
    ]


def test_parse_depfile():
    text = "obj/a.o: ../src/a.cpp ../src/a.hpp \\\n  /usr/include/stdio.h \\\n  ../src/b.h\n"
    assert parse_depfile(text, "build") == ["src/a.cpp", "src/a.hpp", "src/b.h"]
    assert parse_depfile(text, "sub/build") == ["sub/src/a.cpp", "sub/src/a.hpp", "sub/src/b.h"]


def test_parse_depfile_unescapes_spaces():
    text = "a.o: my\\ src/a.c my\\ src/c\\#.h my\\ src/d$$.h \\\n  b.h\n"
    assert parse_depfile(text, "") == ["my src/a.c", "my src/c#.h", "my src/d$.h", "b.h"]
