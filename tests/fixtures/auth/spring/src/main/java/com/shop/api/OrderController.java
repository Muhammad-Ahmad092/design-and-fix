package com.shop.api;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class OrderController {
    @PostMapping("/orders")
    public void create() {}

    @DeleteMapping("/admin/orders/{id}")
    public void purge(@PathVariable String id) {}

    @PostMapping("/users")
    public void register() {}
}
