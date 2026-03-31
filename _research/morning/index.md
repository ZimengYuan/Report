---
layout: default
title: 早间研究
permalink: /research/morning/
---

<h1>早间研究</h1>

<ul>
{% assign morning_reports = site.research | where: "public_report", true | where: "type", "morning" | sort: "title" %}
{% for doc in morning_reports %}
    <li><a href="{{ doc.url | relative_url }}">{{ doc.title }}</a>{% if doc.date %} - {{ doc.date | date: "%Y-%m-%d" }}{% endif %}</li>
{% endfor %}
</ul>
