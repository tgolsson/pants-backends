package main

import "core:fmt"
import "lib/utils"

main :: proc() {
	fmt.println("Hello, Odin!", square(10))
	utils.print_calculation(5, 3)
}
