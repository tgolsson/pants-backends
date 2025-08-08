package math

add :: proc(a, b: int) -> int {
    return a + b
}

multiply :: proc(a, b: int) -> int {
    return a * b
}

power :: proc(base, exp: int) -> int {
    result := 1
    for i in 0..<exp {
        result *= base
    }
    return result
}