"""Database URLs are normalised for the pure-Python driver, and libpq's sslmode is honoured."""

import ssl

from acqbot.db import engine_args


def test_psycopg_scheme_is_rewritten_and_sslmode_require_means_tls_without_verification():
    url, args = engine_args("postgresql+psycopg://u:p@host.example:5432/db?sslmode=require")
    assert url == "postgresql+pg8000://u:p@host.example:5432/db"
    ctx = args["ssl_context"]
    assert isinstance(ctx, ssl.SSLContext) and ctx.verify_mode == ssl.CERT_NONE and not ctx.check_hostname


def test_verify_full_keeps_certificate_verification():
    url, args = engine_args("postgresql://u:p@host.example/db?sslmode=verify-full&sslrootcert=x.pem")
    assert url == "postgresql+pg8000://u:p@host.example/db"
    assert args["ssl_context"].verify_mode == ssl.CERT_REQUIRED


def test_local_url_gets_no_tls_and_other_query_params_survive():
    url, args = engine_args("postgresql+pg8000://postgres:postgres@127.0.0.1:5432/acqbot?application_name=x")
    assert url == "postgresql+pg8000://postgres:postgres@127.0.0.1:5432/acqbot?application_name=x"
    assert args == {}
    assert engine_args("sqlite:///x.db") == ("sqlite:///x.db", {})
