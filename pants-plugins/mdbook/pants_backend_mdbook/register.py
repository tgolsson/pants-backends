from pants_backend_mdbook import goals, subsystem, targets, util_rules


def target_types():
    return [
        *targets.targets(),
    ]


def rules():
    return [
        *subsystem.rules(),
        *util_rules.rules(),
        *goals.rules(),
    ]
