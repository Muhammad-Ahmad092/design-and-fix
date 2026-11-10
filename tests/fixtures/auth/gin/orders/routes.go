package orders

import "github.com/gin-gonic/gin"

func Register(router *gin.RouterGroup) {
	router.POST("/", create)
	router.DELETE("/:id", remove)
}
