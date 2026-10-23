package main

import (
	"database/sql"
	"net/http"

	"github.com/gin-gonic/gin"
)

var db *sql.DB

type Order struct {
	ID    int
	Total float64
}

func fetchRates(url string) (*http.Response, error) {
	return http.Get(url)
}

func listOrders(uid int) ([]Order, error) {
	rows, err := db.Query("SELECT id, total FROM orders WHERE user_id = $1", uid)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Order
	for rows.Next() {
		var o Order
		if err := rows.Scan(&o.ID, &o.Total); err != nil {
			return nil, err
		}
		out = append(out, o)
	}
	return out, nil
}

func createOrder(c *gin.Context) {
	c.JSON(http.StatusCreated, gin.H{"ok": true})
}

func main() {
	r := gin.Default()
	r.POST("/orders", createOrder)
	r.GET("/healthz", func(c *gin.Context) {
		c.String(http.StatusOK, "ok")
	})
	_ = r.Run(":8080")
}
