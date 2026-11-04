#include "greet.h"

#include <stdio.h>

int greeting(char *buf, size_t size, const char *who) {
    return snprintf(buf, size, "Hello from %s!", who);
}
