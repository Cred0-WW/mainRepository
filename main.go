package main

import (
	"fmt"
	"os"
	"runtime"
	"strings"
)

const Version = "1.2.0"

func procTrim(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return "n/a"
	}
	return strings.TrimSpace(string(b))
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "--version" {
		fmt.Println("syspulse", Version)
		return
	}
	fmt.Println("=== SysPulse ===")
	fmt.Println("version:   ", Version)
	fmt.Println("go:        ", runtime.Version())
	fmt.Println("cpus:      ", runtime.NumCPU())
	fmt.Println("goroutines:", runtime.NumGoroutine())
	fmt.Println("uptime:    ", procTrim("/proc/uptime"))
	fmt.Println("loadavg:   ", procTrim("/proc/loadavg"))
	fmt.Println("meminfo:   ", procTrim("/proc/meminfo"))
}
