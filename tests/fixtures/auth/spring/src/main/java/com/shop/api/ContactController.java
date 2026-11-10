package com.shop.api;

import org.springframework.web.bind.annotation.*;

@RestController
public class ContactController {
    @PostMapping("/public/contact")
    public void contact() {}
}
