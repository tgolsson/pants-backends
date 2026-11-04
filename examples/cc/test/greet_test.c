#include <stdio.h>
#include <string.h>

#include "greet.h"

int main(void) {
    char buf[64];
    int n = greeting(buf, sizeof buf, "test");
    if (strcmp(buf, "Hello from test!") != 0 || n != (int)strlen(buf)) {
        fprintf(stderr, "unexpected greeting: %s (%d)\n", buf, n);
        return 1;
    }
    return 0;
}
