from pants_backend_mdbook import goals, subsystem, target_types_rules, targets, util_rules


def target_types():
    return [
        *targets.targets(),
    ]


def rules():
    return [
        *subsystem.rules(),
        *target_types_rules.rules(),
        *util_rules.rules(),
        *goals.rules(),
    ]
