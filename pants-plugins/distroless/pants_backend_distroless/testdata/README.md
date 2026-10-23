# OpenPGP test fixtures

Throwaway keys and signatures made with GnuPG, so `openpgp_test.py` checks the verifier against an
independent implementation. Never use these keys for anything else.

``` sh
export GNUPGHOME=$(mktemp -d)
gpg --batch --passphrase '' --quick-gen-key 'Test RSA <rsa@example.com>' rsa2048 sign never
gpg --batch --passphrase '' --quick-gen-key 'Test Ed25519 <ed25519@example.com>' ed25519 sign never
gpg --batch --passphrase '' --quick-gen-key 'Test Other <other@example.com>' rsa2048 sign never
RSA=B921132DE0E83D0D ED=101F6007C862E2B2 OTHER=B692EFCFE6D239DF

gpg --armor --export $RSA > rsa.asc
gpg --export $ED > ed25519.gpg
gpg --armor --export $OTHER > other.asc
gpg --export $RSA $ED > keyring.gpg

gpg --armor -u $RSA -u $ED -u $OTHER --digest-algo SHA256 --detach-sign -o Release.gpg Release
gpg -u $RSA -u $ED -u $OTHER --digest-algo SHA512 --clearsign -o InRelease Release
gpg --armor -u $RSA --digest-algo SHA1 --detach-sign -o Release.sha1.gpg Release
gpg --armor -u $OTHER --digest-algo SHA256 --detach-sign -o Release.other.gpg Release
```

`rsa-private.json` holds the `n`, `e` and `d` of the RSA key's unprotected secret key packet
(`gpg --export-secret-keys $RSA`), for the test signer in `openpgp_test.py` that signs the
repositories the resolver tests generate.
