#ifndef GREET_H
#define GREET_H

#include <stddef.h>

/* Writes "Hello from <who>!" into buf, like snprintf, and returns its length. */
int greeting(char *buf, size_t size, const char *who);

#endif
