package com.example.shop;

import java.net.http.HttpClient;
import java.util.List;
import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/orders")
public class OrderController {
    private final JdbcTemplate jdbc;
    private final HttpClient inventory = HttpClient.newHttpClient();

    public OrderController(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    // @PostMapping("/commented-out") is a comment and must not be listed.
    /*
     * @PutMapping("/{id}") appears only in documentation.
     */

    @GetMapping("/{id}")
    public List<Map<String, Object>> find(@PathVariable String id) {
        return jdbc.queryForList("SELECT * FROM orders WHERE id = '" + id + "'");
    }

    @PostMapping
    public void create(@RequestBody Order order) {
        jdbc.update("INSERT INTO orders (sku) VALUES (?)", order.sku());
    }

    @PreAuthorize("hasRole('ADMIN')")
    @DeleteMapping("/{id}")
    public void cancel(@PathVariable String id) {
        jdbc.update("DELETE FROM orders WHERE id = ?", id);
    }

    public record Order(String sku) {
    }
}
