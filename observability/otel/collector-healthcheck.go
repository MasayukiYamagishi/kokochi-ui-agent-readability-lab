package main

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"time"
)

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: collector-healthcheck <health-url>")
		os.Exit(2)
	}

	client := http.Client{Timeout: 1500 * time.Millisecond}
	response, err := client.Get(os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, response.Body)

	if response.StatusCode < http.StatusOK || response.StatusCode >= 300 {
		fmt.Fprintf(os.Stderr, "unhealthy HTTP status: %s\n", response.Status)
		os.Exit(1)
	}
}
