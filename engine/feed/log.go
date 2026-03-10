package feed

import (
	"fmt"
	"time"
)

func logTS(format string, args ...interface{}) {
	prefix := time.Now().Format("2006-01-02 15:04:05.000")
	fmt.Printf(prefix+" "+format+"\n", args...)
}
