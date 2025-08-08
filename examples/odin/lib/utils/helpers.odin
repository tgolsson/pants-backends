package utils

import "core:fmt"
import "../math"

print_calculation :: proc(a, b: int) {
    sum := math.add(a, b)
    product := math.multiply(a, b)
    
    fmt.printf("%d + %d = %d\n", a, b, sum)
    fmt.printf("%d * %d = %d\n", a, b, product)
}