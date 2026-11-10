package main

import (
	"example.com/shop/items"
	"example.com/shop/orders"
	"github.com/gin-gonic/gin"
)

func main() {
	r := gin.Default()
	r.GET("/health", health)
	api := r.Group("/api", AuthRequired())
	orders.Register(api.Group("/orders"))
	items.Register(r.Group("/items"))
	r.Run()
}
