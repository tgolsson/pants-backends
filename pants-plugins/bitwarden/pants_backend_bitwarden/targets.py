from pants.engine.target import COMMON_TARGET_FIELDS, SpecialCasedDependencies, StringField, Target


class BitWardenId(StringField):
    alias = "id"

    help = "A BitWarden ID"


class BitWardenSessionSecret(StringField):
    alias = "session_secret"
    help = "The secret to use for the secret"


class BitWardenItemName(StringField):
    alias = "item_name"

    help = "A BitWarden item name. Not guaranteed to be unique, so prefer using a `BitWardenId`."


class BitWardenItemField(SpecialCasedDependencies):
    alias = "item"

    help = "A reference to a BitWarden Item to use as a source of secrets.."


class BitWardenFieldField(StringField):
    alias = "field_name"

    help = "The name of a field in a BitWarden item."


class BitWardenItem(Target):
    alias = "bw_item"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        BitWardenId,
        BitWardenItemName,
        BitWardenSessionSecret,
    )

    help = "A BitWarden item in the vault."


class BitWardenPassword(Target):
    alias = "bw_password"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        BitWardenItemField,
    )

    help = "The password stored in an Item."


class BitWardenField(Target):
    alias = "bw_item_field"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        BitWardenItemField,
        BitWardenFieldField,
    )

    help = "A specific field in an Item."


def targets():
    return [
        BitWardenItem,
        BitWardenPassword,
        BitWardenField,
    ]
