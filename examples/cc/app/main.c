#include <stdio.h>

#include "greet.h"

int main(void) {
    char buf[64];
    greeting(buf, sizeof buf, "cc");
    puts(buf);
    return 0;
}
