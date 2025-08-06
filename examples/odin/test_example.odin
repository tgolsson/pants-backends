package main

import "core:testing"

square :: proc(x: int) -> int {
    return x * x
}

@(test)
test_square :: proc(t: ^testing.T) {
    result := square(4)
    testing.expect_value(t, result, 16)
}

@(test)
test_square_zero :: proc(t: ^testing.T) {
    result := square(0)
    testing.expect_value(t, result, 0)
}

@(test)
test_square_negative :: proc(t: ^testing.T) {
    result := square(-3)
    testing.expect_value(t, result, 9)
}