env_secret(
    name="bw_session_key",
    key="BW_SESSION",
)

bw_item(
    name="PyPi",
    id="a0d0cd53-f254-46f8-84f6-af34010bdf52",
    session_secret=":bw_session_key",
)

bw_item_field(
    name="pypi_token",
    item=[":PyPi"],
    field_name="API_TOKEN"
)
